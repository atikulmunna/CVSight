from __future__ import annotations

from dataclasses import dataclass

from shelfsight_api.model_contract import ModelProvenance
from shelfsight_api.recognition_profile import EmbeddingProfile


@dataclass(frozen=True)
class PropagationProfile(EmbeddingProfile):
    quality_evidence: str


T005_EXPLORATORY_PROPAGATION_PROFILE = PropagationProfile(
    index_namespace="propagation:clip-vit-b32:t005-exploratory",
    index_version="baseline-001",
    model_provenance=ModelProvenance(
        model_id="openai-clip-vit-b32",
        model_version="3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268",
        artifact_sha256="a63082132ba4f97a80bea76823f544493bffa8082296d62d71581a4feff1576f",
    ),
    dimension=512,
    quality_evidence="t005-top1-0.5000-hard-pair-confusion-0.1333",
)
