"""
This script generate embedding from unique words in the corpus.

This script works with the gpu-linux-cuda118 environment using CUDA/11.8 on a100 GPUs.
If using CPUs, reinstall torch that is compatable with CPU-only machines.

Authors: Parker Hicks, Hao Yuan
Date: 2024-11-28

Last updated: 2026-02-16 by Parker Hicks
"""

from argparse import ArgumentParser
from pathlib import Path

import numpy as np
import numpy.typing as npt
import polars as pl
import torch
from tqdm import tqdm
from transformers import AutoModel, AutoTokenizer

EMBEDDING_LEVELS: tuple[str, str] = ("word", "document")
SUPPORTED_LLMS: dict[str, str] = {
    "biomedbert": "microsoft/BiomedNLP-BiomedBERT-base-uncased-abstract",
    "biomedlm": "stanford-crfm/BioMedLM",
    "biomed_electra": "microsoft/BiomedNLP-BiomedELECTRA-base-uncased-abstract",
    "sapbert": "cambridgeltl/SapBERT-from-PubMedBERT-fulltext",
}


class EmbeddingGenerator:
    """This class is used to generate an embedding matrix for words or documents.

    Attributes:
        model_name (str):
            Name of LLM to load.

            - Options are:
                biomedbert (preferred)
                biomedlm
                biomed_electra
                sapbert

        data (NpStrArray | list[str]):
            Array of text documents.

        level (str):
            Level of embeddings to compute. Either 'word' or 'document'.

            - Word-level embeddings will result in a matrix of shape:
                 (number of unique words x embedding dimension)

            - Document-level embeddings will give a matrix of shape:
                 (number of documents x embedding dimension)

        tokenizer (transformers.Autotokenizer):
            Tokenizer object to split text into tokens.

        model (transformers.Automodel):
            Transfomers model object to pass tokens through to get embeddings.

        embedding_size (int):
            Dimension of embedding vectors for the specified model.

        embeddings_array (torch.tensor):
            Tensor of shape (number of features, embedding dimension).

    _features (NpStrArray)
        Features to pass through the LLM. Either words or entire documents.


    Properties:
        device (str):
            Returns either 'cuda' or 'cpu' if CUDA is available.

        embeddings (torch.tensor):
            Returns self.embeddings_array.

        features (NpStrArray):
            Returns features representing each embedding except for document-level features.

    """

    def __init__(self, data, level, model: str = "biomedbert"):
        self.data: npt.NDArray = np.array(data)
        self.level: str = level
        self.model_name: str = model
        self.embeddings_array: torch.Tensor | None = None
        self._features: npt.NDArray = None

        # load LLM
        self._load_language_model(model)

        # set model features
        self._set_features()

    def get_model_url(self, model: str):
        """Assigns a url to load the specified model from HuggingFace."""
        if model not in SUPPORTED_LLMS:
            raise ValueError(
                f"Invalid model name. Please choose from {list(SUPPORTED_LLMS)}."
            )

        return SUPPORTED_LLMS[model]

    def _load_language_model(self, model: str) -> None:
        """Load the language model from HuggingFace."""
        print(f"Loading {model}")
        url = self.get_model_url(model)
        print(f"Using url: {url}")
        self.tokenizer = AutoTokenizer.from_pretrained(url)
        self.model = AutoModel.from_pretrained(url)
        self.embedding_size = getattr(
            self.model.config, "hidden_size", None
        ) or getattr(self.model.config, "n_embd")
        print(f"Embedding size: {self.embedding_size}")

        if model == "biomedlm":  # BioMedLM requires a padding token
            print("Added padding token for biomedlm")
            self.tokenizer.pad_token = self.tokenizer.eos_token

        self.model.to(self.device)
        self.model.eval()

        print("Loading successful")
        print(f"Using device: {self.device}")

    def _set_features(self) -> None:
        """Assigns features given word or document level operations."""
        if self.level == "word":
            self._features = self.unique_words()
        elif self.level == "document":
            self._features = self.data
        else:
            supported = EMBEDDING_LEVELS
            raise ValueError(f"Expected level in {supported}. Got {self.level}.")

    def generate_embedding(self, text: str | list[str]) -> torch.Tensor:
        """Generates embeddings for a batch of one or more features."""
        encoded_input = self.tokenizer(
            text, padding=True, truncation=True, return_tensors="pt", max_length=512
        )
        input_ids = encoded_input["input_ids"].to(self.device)
        attention_mask = encoded_input["attention_mask"].to(self.device)

        # forward pass
        with torch.no_grad():
            outputs = self.model(input_ids, attention_mask=attention_mask)

        if self.model_name == "sapbert":
            # SapBERT's reference usage takes the [CLS] token representation
            return outputs.last_hidden_state[:, 0, :]

        # mean-pool token embeddings, ignoring padding tokens
        mask = (
            attention_mask.unsqueeze(-1)
            .expand(outputs.last_hidden_state.size())
            .float()
        )
        summed = torch.sum(outputs.last_hidden_state * mask, dim=1)
        counts = torch.clamp(mask.sum(dim=1), min=1e-9)
        return summed / counts

    def generate_embeddings(self, batch_size: int = 32) -> None:
        """Generates an embedding for every feature of the specified level."""
        num_features = len(self._features)
        self.embeddings_array = torch.zeros(
            (num_features, self.embedding_size), device=self.device
        )
        num_batches = -(-num_features // batch_size)
        for start in tqdm(
            range(0, num_features, batch_size),
            total=num_batches,
            desc="Generating embeddings...",
        ):
            batch = self._features[start : start + batch_size].tolist()
            self.embeddings_array[start : start + len(batch)] = self.generate_embedding(
                batch
            )

    def to_parquet(self, file: str | Path):
        """Save embedding matrix as a parquet file with words as columns."""

        if self.level == "word":
            lf = pl.LazyFrame(self.embeddings.T, schema=list(self.features))
            lf.sink_parquet(file)

    def unique_words(self) -> npt.NDArray:
        """Get the unique words in a corpus."""
        return (
            pl.Series(self.data)
            .str.extract_all(r"\S+")
            .explode()
            .unique()
            .to_numpy()
            .astype(str)
        )

    @property
    def device(self):
        """Return gpu or cpu depending on availability."""
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")

    @property
    def embeddings(self):
        """Return the embeddings matrix of shape (words x embedding_size)."""
        if self.embeddings_array is None:
            self.generate_embeddings()

        return self.embeddings_array.detach().cpu().numpy()

    @property
    def features(self) -> npt.NDArray:
        """Return word features per embedding."""
        if self.level == "document":
            raise ValueError(
                "Feature level {self.level} cannot be returned as features."
            )
        if self._features is None:
            raise ValueError(
                "Attempting to call features when none were generated or assigned."
            )
        return self._features.astype(str)


def main():
    parser = ArgumentParser()
    parser.add_argument(
        "-i",
        "--input",
        help="Path to file containing a free-text document per line.",
        type=Path,
        required=True,
    )
    parser.add_argument(
        "-l",
        "--level",
        help="Word or doument level. If word, will extract unique words to embed.",
        type=str,
        choices=["word", "document"],
        default="word",
    )
    parser.add_argument(
        "-m",
        "--model",
        help="LLM to use for embedding.",
        choices=list(SUPPORTED_LLMS.keys()),
        default="sapbert",
    )
    parser.add_argument(
        "-o",
        "--outfile",
        help="Path to embeddings.parquet",
        type=Path,
        default="emebddings.parquet",
    )
    args = parser.parse_args()

    with open(args.input, "r", encoding="utf-8") as f:
        documents = [line.strip() for line in f.readlines()]

    generator = EmbeddingGenerator(documents, level=args.level, model=args.model)
    generator.generate_embeddings()
    generator.to_parquet(args.outfile)


if __name__ == "__main__":
    main()
