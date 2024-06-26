import json
from typing import Any, Dict, Iterable, List
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import ray
import torch
import typer
from numpyencoder import NumpyEncoder
from ray.air import Result
from ray.train.torch import TorchPredictor
from ray.train.torch.torch_checkpoint import TorchCheckpoint
from typing_extensions import Annotated
from mlflow.tracking import MlflowClient

import os
from data import load_pickle
from config import logger, mlflow
from pathlib import Path

# Initialize Typer CLI app
app = typer.Typer()



def decode(indices: Iterable[Any], index_to_class: Dict) -> List:
    """Decode indices to labels.

    Args:
        indices (Iterable[Any]): Iterable (list, array, etc.) with indices.
        index_to_class (Dict): mapping between indices and labels.

    Returns:
        List: list of labels.
    """
    return [index_to_class[index] for index in indices]


def format_prob(prob: Iterable, index_to_class: Dict) -> Dict:
    """Format probabilities to a dictionary mapping class label to probability.

    Args:
        prob (Iterable): probabilities.
        index_to_class (Dict): mapping between indices and labels.

    Returns:
        Dict: Dictionary mapping class label to probability.
    """
    d = {}
    for i, item in enumerate(prob):
        d[index_to_class[i]] = item
    return d


def predict_with_proba(
    data: pd.DataFrame,
    predictor: ray.train.torch.torch_predictor.TorchPredictor,
) -> List:  # pragma: no cover, tested with inference workload
    """Predict tags (with probabilities) for input data from a dataframe.

    Args:
        df (pd.DataFrame): dataframe with input features.
        predictor (ray.train.torch.torch_predictor.TorchPredictor): loaded predictor from a checkpoint.

    Returns:
        List: list of predicted labels.
    """
    preprocessor = predictor.get_preprocessor()
    z = predictor.predict(data, dtype=torch.float)["predictions"]
    y_prob = torch.tensor(np.stack(z)).softmax(dim=1).numpy()
    print('probabilities:', y_prob)
    results = []
    for i, prob in enumerate(y_prob):
        tag = decode([z[i].argmax()], preprocessor.index_to_class)[0]
        results.append({"prediction": tag, "probabilities": format_prob(prob, preprocessor.index_to_class)})
    return results


@app.command()
def get_best_run_id(experiment_name: str = "", metric: str = "", mode: str = "") -> str:  # pragma: no cover, mlflow logic
    """Get the best run_id from an MLflow experiment.

    Args:
        experiment_name (str): name of the experiment.
        metric (str): metric to filter by.
        mode (str): direction of metric (ASC/DESC).

    Returns:
        str: best run id from experiment.
    """
    sorted_runs = mlflow.search_runs(
        experiment_names=[experiment_name],
        order_by=[f"metrics.{metric} {mode}"],
    )
    run_id = sorted_runs.iloc[0].run_id
    print(run_id)
    return run_id


def get_best_checkpoint(run_id: str) -> TorchCheckpoint:  # pragma: no cover, mlflow logic
    """Get the best checkpoint from a specific run.

    Args:
        run_id (str): ID of the run to get the best checkpoint from.

    Returns:
        TorchCheckpoint: Best checkpoint from the run.
    """
    # Downloads the artifacts first
    client = MlflowClient()

    artifacts_dir = 'mlops/artifacts'

    if not os.path.exists(artifacts_dir):
        os.makedirs(artifacts_dir)

    mlflow.tracking.MlflowClient().download_artifacts(run_id, '', artifacts_dir)

    # Used if stored locally
    # artifact_dir = urlparse(mlflow.get_run(run_id).info.artifact_uri).path  # get path from mlflow
    results = Result.from_path(artifacts_dir)
    return results.best_checkpoints[0][0]


@app.command()
def predict(
    run_id: Annotated[str, typer.Option(help="id of the specific run to load from")] = None,
    dataset_loc: Annotated[str, typer.Option(help="location of the dataset")] = None,
    subset: Annotated[str, typer.Option(help="the name of the data subset")] = "test",
    participant: Annotated[int, typer.Option(help="participant id")] = 0
) -> Dict:  # pragma: no cover, tested with inference workload
    """Predict the tag for a project given it's title and description.

    Args:
        run_id (str): id of the specific run to load from. Defaults to None.
        features (np.ndarray, optional): matrix of features per frame [4,800] . Defaults to "".

    Returns:
        Dict: prediction results for the input data.
    """
    # Load components
    best_checkpoint = get_best_checkpoint(run_id=run_id)
    predictor = TorchPredictor.from_checkpoint(best_checkpoint)
    preprocessor = predictor.get_preprocessor()

    # Predict
    fpath = os.path.join(dataset_loc, subset + "_participant" + str(participant) + ".pkl")
    data, targets, groups = load_pickle(fpath)
    ds = pd.DataFrame({'groups': groups, 'targets': targets})
    ds['id'] = ds.index.values.tolist()
    ds['features'] = ds['id'].apply(lambda r: data[r])
    results = predict_with_proba(data=ds, predictor=predictor)
    logger.info(json.dumps(results, cls=NumpyEncoder, indent=2))
    return results


if __name__ == "__main__":  # pragma: no cover, application
    app()
