"""
Initialize a FAISS vector database and execute queries to retrieve the top k similar candidate
vectors for a particular set of query vectors.

Author: Parker Hicks
Date: 2026-09-14
"""

from enum import Enum

import faiss
import numpy as np
import numpy.typing as npt


class FaissMetric(Enum):
    """FAISS similarity metric."""

    COSINE = "cosine"
    DOT = "dot"
    L2 = "l2"


class FaissDevice(Enum):
    """Compute device."""

    CPU = "cpu"
    GPU = "gpu"


class Faiss:
    def __init__(self, metric: str | FaissMetric, device: str | FaissDevice):
        self.metric = FaissMetric(metric)
        self.device = FaissDevice(device)

    def build(self, data: npt.NDArray[np.float32]) -> faiss.Index:
        """Populate the FAISS index with candidate vectors.

        Arguments:
            data (npt.NDArray[np.int32]):
                A row-oriented 2-dimensional numpy array.

        Returns:
            (faiss.Index): An initialized vector database populated with candidate vectors.
        """
        dim = data.shape[1]

        match self.metric:
            case FaissMetric.COSINE:
                index = faiss.IndexFlatIP(dim)  # inner product
                faiss.normalize_L2(vectors)
            case FaissMetric.DOT:
                index = faiss.IndexFlatIP(dim)
            case FaissMetric.L2:
                index = faiss.IndexFlatL2(dim)  # euclidean distance
            case _:
                raise ValueError(f"Unrecognized index: {self.metric.value}.")

        match self.device:
            case FaissDevice.GPU:
                manager = faiss.StandardGpuResources()
                index = faiss.index_cpu_to_gpu(manager, 0, index)

        index.add(data)

        return index

    def query(
        self, index: faiss.Index, query: npt.NDArray[np.float32], k: int = 100
    ) -> tuple[npt.NDArray, npt.NDArray]:
        """Find the top k similar vectors a query vector or multiple queries.

        Arguments:
            query (npt.NDArray[np.float32]):
                A row-oriented 1D or 2D numpy array.
            k (int):
                The top k nearest neighbors to return.

        Returns:
            (tuple[npt.NDArray, npt.NDArray]): The top k similarity values and the
                associated indices of the candidate vectors.

        """
        match self.metric:
            case FaissMetric.COSINE:
                faiss.normalize_L2(query)

        similarities, indices = index.search(query, k)

        if k > 1:
            return similarities, indices

        return similarities.flatten(), indices.flatten()


def main():
    """Main entry point."""


if __name__ == "__main__":
    main()
