from __future__ import annotations

from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    TypeAdapter,
    model_validator,
)

MAX_IMAGE_PIXELS = 120_000_000
MAX_PREDICTIONS = 5_000
MAX_EMBEDDING_DIMENSION = 4_096
MAX_RETRIEVAL_CANDIDATES = 100


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AxisAlignedBox(ContractModel):
    type: Literal["axis_aligned_box"] = "axis_aligned_box"
    x: Annotated[FiniteFloat, Field(ge=0)]
    y: Annotated[FiniteFloat, Field(ge=0)]
    width: Annotated[FiniteFloat, Field(gt=0)]
    height: Annotated[FiniteFloat, Field(gt=0)]


class ImageInput(ContractModel):
    image_id: UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: Annotated[int, Field(gt=0)]
    height: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def validate_pixel_limit(self) -> ImageInput:
        if self.width * self.height > MAX_IMAGE_PIXELS:
            raise ValueError("image exceeds the decoded pixel limit")
        return self


class DetectConfiguration(ContractModel):
    confidence_threshold: Annotated[FiniteFloat, Field(ge=0, le=1)] = 0.3
    inference_strategy: Literal["full_image", "dense_retry"] = "full_image"


class RefineConfiguration(ContractModel):
    return_geometry: Literal["axis_aligned_box"] = "axis_aligned_box"


class EmbedConfiguration(ContractModel):
    normalize: Literal[True] = True


class RetrieveConfiguration(ContractModel):
    distance: Literal["cosine"] = "cosine"
    calibration_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )


class ImageOperationRequest(ContractModel):
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    image: ImageInput
    model_role: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z][a-z0-9_]*$",
    )


class DetectRequest(ImageOperationRequest):
    operation: Literal["detect"]
    configuration: DetectConfiguration = Field(default_factory=DetectConfiguration)


class RefineRequest(ImageOperationRequest):
    operation: Literal["refine"]
    box_hint: AxisAlignedBox
    configuration: RefineConfiguration = Field(default_factory=RefineConfiguration)

    @model_validator(mode="after")
    def validate_box_bounds(self) -> RefineRequest:
        validate_box_bounds(self.box_hint, self.image)
        return self


class AnnotationEmbeddingTarget(ContractModel):
    type: Literal["annotation"]
    annotation_id: UUID
    annotation_revision: Annotated[int, Field(gt=0)]


class SkuReferenceEmbeddingTarget(ContractModel):
    type: Literal["sku_reference"]
    reference_image_id: UUID


EmbeddingTarget = Annotated[
    AnnotationEmbeddingTarget | SkuReferenceEmbeddingTarget,
    Field(discriminator="type"),
]


class EmbedRequest(ImageOperationRequest):
    operation: Literal["embed"]
    crop: AxisAlignedBox
    purpose: Literal["recognition", "propagation"]
    target: EmbeddingTarget | None = None
    configuration: EmbedConfiguration = Field(default_factory=EmbedConfiguration)

    @model_validator(mode="after")
    def validate_crop_bounds(self) -> EmbedRequest:
        validate_box_bounds(self.crop, self.image)
        return self


class RetrieveRequest(ContractModel):
    request_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    operation: Literal["retrieve"]
    embedding_id: UUID
    purpose: Literal["recognition", "propagation"]
    index_namespace: str = Field(
        min_length=1,
        max_length=255,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    index_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    top_k: Annotated[int, Field(ge=1, le=MAX_RETRIEVAL_CANDIDATES)] = 5
    configuration: RetrieveConfiguration


ModelRequest = Annotated[
    DetectRequest | RefineRequest | EmbedRequest | RetrieveRequest,
    Field(discriminator="operation"),
]
request_adapter: TypeAdapter[ModelRequest] = TypeAdapter(ModelRequest)


class ModelProvenance(ContractModel):
    model_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    model_version: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:@-]*$",
    )
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class IndexProvenance(ContractModel):
    index_namespace: str = Field(min_length=1, max_length=255)
    index_version: str = Field(min_length=1, max_length=128)
    purpose: Literal["recognition", "propagation"]


class Timing(ContractModel):
    total: Annotated[FiniteFloat, Field(ge=0)]


class Prediction(ContractModel):
    proposal_id: str = Field(min_length=1, max_length=128)
    geometry: AxisAlignedBox
    class_type: Literal["product", "gap", "shelf_label"]
    score: Annotated[FiniteFloat, Field(ge=0, le=1)]
    candidate_sku_id: UUID | None = None


class RefineResult(ContractModel):
    geometry: AxisAlignedBox
    score: Annotated[FiniteFloat, Field(ge=0, le=1)]


class Embedding(ContractModel):
    embedding_id: UUID
    purpose: Literal["recognition", "propagation"]
    dimension: Annotated[int, Field(ge=1, le=MAX_EMBEDDING_DIMENSION)]
    normalized: Literal[True]
    values: list[FiniteFloat] = Field(
        min_length=1,
        max_length=MAX_EMBEDDING_DIMENSION,
    )

    @model_validator(mode="after")
    def validate_dimension(self) -> Embedding:
        if self.dimension != len(self.values):
            raise ValueError("embedding dimension does not match its values")
        magnitude = sum(float(value) ** 2 for value in self.values)
        if not 0.999 <= magnitude <= 1.001:
            raise ValueError("embedding must have unit magnitude")
        return self


class RetrievalCandidate(ContractModel):
    rank: Annotated[int, Field(gt=0)]
    subject_type: Literal["sku", "annotation"]
    subject_id: UUID
    score: Annotated[FiniteFloat, Field(ge=-1, le=1)]


class UnknownDecision(ContractModel):
    is_unknown: bool
    threshold: Annotated[FiniteFloat, Field(ge=-1, le=1)]
    calibration_version: str = Field(min_length=1, max_length=128)
    automatic_confirmation: Literal[False] = False


class BaseModelResponse(ContractModel):
    request_id: str
    input_fingerprint: str = Field(min_length=1, max_length=128)
    timing_ms: Timing


class DetectResponse(BaseModelResponse):
    operation: Literal["detect"]
    configuration: DetectConfiguration
    model_provenance: ModelProvenance
    predictions: list[Prediction] = Field(max_length=MAX_PREDICTIONS)


class RefineResponse(BaseModelResponse):
    operation: Literal["refine"]
    configuration: RefineConfiguration
    model_provenance: ModelProvenance
    result: RefineResult


class EmbedResponse(BaseModelResponse):
    operation: Literal["embed"]
    configuration: EmbedConfiguration
    model_provenance: ModelProvenance
    embedding: Embedding


class RetrieveResponse(BaseModelResponse):
    operation: Literal["retrieve"]
    configuration: RetrieveConfiguration
    index_provenance: IndexProvenance
    candidates: list[RetrievalCandidate] = Field(
        max_length=MAX_RETRIEVAL_CANDIDATES
    )
    unknown_decision: UnknownDecision

    @model_validator(mode="after")
    def validate_candidate_order(self) -> RetrieveResponse:
        expected_ranks = list(range(1, len(self.candidates) + 1))
        if [candidate.rank for candidate in self.candidates] != expected_ranks:
            raise ValueError("retrieval candidate ranks must be contiguous")
        if len(self.candidates) > 1 and any(
            left.score < right.score
            for left, right in zip(
                self.candidates,
                self.candidates[1:],
                strict=False,
            )
        ):
            raise ValueError("retrieval candidates must be score ordered")
        return self


ModelResponse = Annotated[
    DetectResponse | RefineResponse | EmbedResponse | RetrieveResponse,
    Field(discriminator="operation"),
]
response_adapter: TypeAdapter[ModelResponse] = TypeAdapter(ModelResponse)


def parse_model_request(value: Any) -> ModelRequest:
    return request_adapter.validate_python(value)


def parse_model_response(value: Any) -> ModelResponse:
    return response_adapter.validate_python(value)


def validate_box_bounds(box: AxisAlignedBox, image: ImageInput) -> None:
    if box.x + box.width > image.width or box.y + box.height > image.height:
        raise ValueError("box exceeds canonical image bounds")
