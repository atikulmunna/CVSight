from __future__ import annotations

from dataclasses import dataclass

from shelfsight_api.model_contract import ModelProvenance


@dataclass(frozen=True)
class EmbeddingProfile:
    index_namespace: str
    index_version: str
    model_provenance: ModelProvenance
    dimension: int


@dataclass(frozen=True)
class RecognitionProfile(EmbeddingProfile):
    calibration_version: str
    unknown_threshold: float


T005_RECOGNITION_PROFILE = RecognitionProfile(
    index_namespace="recognition:clip-vit-b32:t005",
    index_version="baseline-001",
    model_provenance=ModelProvenance(
        model_id="openai-clip-vit-b32",
        model_version="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        artifact_sha256="a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f",
    ),
    dimension=512,
    calibration_version="t005-validation-balanced-001",
    unknown_threshold=0.8575034683203777,
)
