from pathlib import Path

import pytest

from benchmark_tool.sam_image import convert_output, validate_options


class FakeTensor:
    def __init__(self, value):
        self.value = value

    def detach(self):
        return self

    def cpu(self):
        return self

    def tolist(self):
        return self.value


def test_convert_output_clips_boxes_and_discards_empty_boxes() -> None:
    output = {
        "boxes": FakeTensor([[-2, 4, 12, 14], [8, 3, 8, 7]]),
        "scores": FakeTensor([0.75, 0.5]),
    }

    assert convert_output(output, "product", width=10, height=10) == [
        {"label": "product", "bbox": [0.0, 4.0, 10.0, 6.0], "score": 0.75}
    ]


def test_validate_options_rejects_invalid_inputs(tmp_path: Path) -> None:
    checkpoint = tmp_path / "sam3.pt"
    checkpoint.write_bytes(b"checkpoint")

    with pytest.raises(ValueError, match="prompt"):
        validate_options(checkpoint, " ", 0.5)
    with pytest.raises(ValueError, match="threshold"):
        validate_options(checkpoint, "product", 1.1)
    with pytest.raises(ValueError, match="checkpoint"):
        validate_options(tmp_path / "missing.pt", "product", 0.5)
