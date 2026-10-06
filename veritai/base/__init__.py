"""Base própria de evidências e checagens (SQLite), com busca BM25 + vetorial."""

from .busca import BaseEvidencias, BaseNaoIndexada

__all__ = ["BaseEvidencias", "BaseNaoIndexada"]
