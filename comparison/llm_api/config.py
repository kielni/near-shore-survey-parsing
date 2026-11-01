"""Settings for the LLM service: which models are run, and where results go."""

from pathlib import Path

# Every model here is run by extract and map; write results to separate directories.
# MODELS = ["gpt-6-sol", "gpt-6-astra"]
MODELS = ["gpt-6-sol"]


def results_dir(model: str) -> Path:
    """Return the results folder for one model."""
    return Path("data/results/llm_api", model)
