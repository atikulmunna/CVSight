from __future__ import annotations

from shelfsight_api.tfrecord import (
    bytes_feature,
    example_bytes,
    float_feature,
    int64_feature,
    tfrecord_bytes,
)

# Golden values produced by TensorFlow 2.21: tf.train.Example.SerializeToString with
# deterministic=True, and tf.io.TFRecordWriter. The encoder must match them byte for byte.
TENSORFLOW_EXAMPLE = (
    "0aaf010a1f0a0e696d6167652f66696c656e616d65120d0a0b0a097368656c662e6a70670a160a0c696d6167"
    "652f68656967687412061a040a02e0030a260a16696d6167652f6f626a6563742f62626f782f786d696e120c"
    "120a0a080000803e0000003f0a2d0a18696d6167652f6f626a6563742f636c6173732f6c6162656c12111a0f"
    "0a0d01ac02feffffffffffffffff010a1d0a17696d6167652f6f626a6563742f636c6173732f746578741202"
    "0a00"
)
TENSORFLOW_RECORDS = (
    "0700000000000000bbd79f11637673696768749f317732000000000000000029039807d8ea82a2"
)


def test_examples_match_tensorflow_serialization() -> None:
    encoded = example_bytes(
        {
            "image/filename": bytes_feature([b"shelf.jpg"]),
            "image/height": int64_feature([480]),
            "image/object/bbox/xmin": float_feature([0.25, 0.5]),
            # 300 needs a two-byte varint; -2 needs the ten-byte two's complement form.
            "image/object/class/label": int64_feature([1, 300, -2]),
            "image/object/class/text": bytes_feature([]),
        }
    )

    assert encoded.hex() == TENSORFLOW_EXAMPLE


def test_record_framing_matches_tensorflow_writer() -> None:
    assert tfrecord_bytes([b"cvsight", b""]).hex() == TENSORFLOW_RECORDS


def test_empty_lists_are_valid_features() -> None:
    assert float_feature([]) == b"\x12\x00"
    assert int64_feature([]) == b"\x1a\x00"
