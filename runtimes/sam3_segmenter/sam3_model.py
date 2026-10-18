from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from PIL import Image

from runtimes.sam3_segmenter.service import SegmentedBox


class Sam3Segmenter:
    """SAM 3's image model with instance interactivity, the SAM 1 style click task.

    SAM 3.1 ships only a video tracking checkpoint, so the SAM 3 image checkpoint is the
    newest one that segments a single photo from a click.
    """

    model_id = "sam3-image-interactive"

    def __init__(self, checkpoint_path: Path) -> None:
        if not checkpoint_path.is_file():
            raise FileNotFoundError(f"SAM 3 checkpoint not found: {checkpoint_path}")
        # Imported here so the service and its tests run without PyTorch or SAM 3.
        import numpy
        import torch
        from sam3 import build_sam3_image_model
        from sam3.model.sam3_image_processor import Sam3Processor

        if not torch.cuda.is_available():
            raise RuntimeError("SAM 3 needs a CUDA GPU")
        torch.backends.cuda.matmul.allow_tf32 = True
        torch.backends.cudnn.allow_tf32 = True
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
        numpy = self._numpy
        with self._torch.inference_mode(), self._torch.autocast("cuda", dtype=self._torch.bfloat16):
            # Three candidate masks (part, object, group) answer an ambiguous single click;
            # the one SAM scores highest is kept.
            masks, scores, _ = self._model.predict_inst(
                encoded,
                point_coords=numpy.array([[x, y]]),
                point_labels=numpy.array([1]),
                multimask_output=True,
            )
        best = int(numpy.argmax(scores))
        return mask_box(numpy.asarray(masks[best]) > 0, float(scores[best]))


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
