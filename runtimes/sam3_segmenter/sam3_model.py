from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from PIL import Image

from runtimes.sam3_segmenter.service import (
    MAX_FOUND_OBJECTS,
    MAX_POLYGON_POINTS,
    PromptPoint,
    Region,
    SegmentedBox,
    SegmentedPolygon,
)

# Found objects come many at a time, so their outlines are traced a little coarser than
# a clicked outline to keep the answer small.
FOUND_OUTLINE_TOLERANCE = 1.0


class Sam3Segmenter:
    """SAM 3's image model with instance interactivity, the SAM 1 style click task.

    SAM 3.1 ships only a video tracking checkpoint, so the SAM 3 image checkpoint is the
    newest one that segments a single photo from a click.
    """

    model_id = "sam3-image-interactive"

    def __init__(self, checkpoint_path: Path) -> None:
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"SAM 3 checkpoint not found: {checkpoint_path}")
        # Imported here so the service and its tests run without PyTorch, OpenCV, or SAM 3.
        import cv2
        import numpy
        import torch
        from sam3 import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        if not torch.cuda.is_available():
            raise RuntimeError("SAM 3 needs a CUDA GPU")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
        self._cv2 = cv2
        self._numpy = numpy
        self._torch = torch
        self._model = build_sam3_image_model(
            checkpoint_path=str(checkpoint_path),
            load_from_HF=False,
            enable_inst_interactivity=True,
        )
        self._processor = Sam3Processor(self._model)
        self.model_version = f"sha256:{_sha256(checkpoint_path)[:16]}"

    def encode(self, image: Image.Image) -> Any:
        with self._torch.inference_mode(), self._torch.autocast("cuda", dtype=self._torch.bfloat16):
            return self._processor.set_image(image)

    def segment(self, encoded: Any, x: float, y: float) -> SegmentedBox | None:
        mask, score = self._predict(encoded, [PromptPoint(x, y, include=True)])
        return mask_box(mask, score)

    def outline(self, encoded: Any, points: Sequence[PromptPoint]) -> SegmentedPolygon | None:
        mask, score = self._predict(encoded, points)
        corners = self._mask_polygon(mask)
        if corners is None:
            return None
        return SegmentedPolygon(points=corners, score=min(max(score, 0.0), 1.0))

    def find(
        self,
        encoded: Any,
        text: str | None,
        exemplars: Sequence[Region],
        threshold: float,
    ) -> list[SegmentedPolygon]:
        """SAM 3's concept prompt: every object matching a phrase, example boxes, or both.

        The prompts are added to the photo's cached encoding and always removed again, so
        later clicks on the photo see the encoding as it was.
        """
        processor = self._processor
        width, height = encoded["original_width"], encoded["original_height"]
        processor.set_confidence_threshold(threshold)
        try:
            with (
                self._torch.inference_mode(),
                self._torch.autocast("cuda", dtype=self._torch.bfloat16),
            ):
                if text:
                    processor.set_text_prompt(text, encoded)
                for box in exemplars:
                    # SAM 3 takes example boxes as normalized centre, width, and height.
                    processor.add_geometric_prompt(
                        [
                            (box.x + box.width / 2) / width,
                            (box.y + box.height / 2) / height,
                            box.width / width,
                            box.height / height,
                        ],
                        True,
                        encoded,
                    )
            masks, scores = encoded.get("masks"), encoded.get("scores")
            if masks is None or len(masks) == 0:
                return []
            found = []
            for index in scores.argsort(descending=True)[:MAX_FOUND_OBJECTS].tolist():
                corners = self._mask_polygon(masks[index, 0].cpu().numpy(), FOUND_OUTLINE_TOLERANCE)
                if corners is not None:
                    score = min(max(float(scores[index]), 0.0), 1.0)
                    found.append(SegmentedPolygon(points=corners, score=score))
            return found
        finally:
            processor.reset_all_prompts(encoded)

    def _predict(self, encoded: Any, points: Sequence[PromptPoint]) -> tuple[Any, float]:
        numpy = self._numpy
        # A single click is ambiguous (part, object, or group), so SAM offers three masks
        # and the best scored one is kept. Further clicks settle what is meant, and SAM
        # advises a single mask for them.
        with self._torch.inference_mode(), self._torch.autocast("cuda", dtype=self._torch.bfloat16):
            masks, scores, _ = self._model.predict_inst(
                encoded,
                point_coords=numpy.array([[point.x, point.y] for point in points]),
                point_labels=numpy.array([1 if point.include else 0 for point in points]),
                multimask_output=len(points) == 1,
            )
        best = int(numpy.argmax(scores))
        return numpy.asarray(masks[best]) > 0, float(scores[best])

    def _mask_polygon(self, mask: Any, tolerance: float = 0.5) -> list[tuple[float, float]] | None:
        """The outline of the largest region of a boolean mask, in pixels. The tolerance
        grows only to stay within the point limit."""
        cv2 = self._cv2
        contours, _ = cv2.findContours(
            mask.astype(self._numpy.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE
        )
        if not contours:
            return None
        contour = max(contours, key=cv2.contourArea)
        # The default half pixel keeps every visible bend; the canvas thins the outline
        # further.
        corners = cv2.approxPolyDP(contour, tolerance, True)
        while len(corners) > MAX_POLYGON_POINTS:
            tolerance *= 2
            corners = cv2.approxPolyDP(contour, tolerance, True)
        if len(corners) < 3:
            return None
        return [(float(x), float(y)) for [[x, y]] in corners]


def mask_box(mask: Any, score: float) -> SegmentedBox | None:
    """The tight box around a boolean height by width mask, in pixels."""
    rows = mask.any(axis=1).nonzero()[0]
    columns = mask.any(axis=0).nonzero()[0]
    if rows.size == 0:
        return None
    return SegmentedBox(
        x=float(columns[0]),
        y=float(rows[0]),
        width=float(columns[-1] - columns[0] + 1),
        height=float(rows[-1] - rows[0] + 1),
        score=min(max(score, 0.0), 1.0),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
