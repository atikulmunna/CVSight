"""Encode tf.train.Example records and TFRecord files without TensorFlow.

Only the few protobuf messages a TFRecord detection dataset needs are written, by hand:
Example, Features, Feature, and the bytes, float, and int64 lists.
"""

from __future__ import annotations

import struct
from collections.abc import Iterable, Mapping, Sequence

import google_crc32c

_LENGTH_DELIMITED = 2


def bytes_feature(values: Sequence[bytes]) -> bytes:
    return _field(1, b"".join(_field(1, value) for value in values))


def float_feature(values: Sequence[float]) -> bytes:
    packed = struct.pack(f"<{len(values)}f", *values)
    return _field(2, _field(1, packed) if values else b"")


def int64_feature(values: Sequence[int]) -> bytes:
    # Negative values use the 64-bit two's complement varint, as protobuf requires.
    packed = b"".join(_varint(value & 0xFFFFFFFFFFFFFFFF) for value in values)
    return _field(3, _field(1, packed) if values else b"")


def example_bytes(features: Mapping[str, bytes]) -> bytes:
    """Serialize an Example from encoded features, keyed by feature name."""
    entries = b"".join(
        _field(1, _field(1, name.encode("utf-8")) + _field(2, feature))
        for name, feature in sorted(features.items())
    )
    return _field(1, entries)


def tfrecord_bytes(records: Iterable[bytes]) -> bytes:
    """Frame records the way TFRecordReader expects: length, masked CRC-32C, data, CRC."""
    framed = bytearray()
    for record in records:
        length = struct.pack("<Q", len(record))
        framed += length
        framed += struct.pack("<I", _masked_crc(length))
        framed += record
        framed += struct.pack("<I", _masked_crc(record))
    return bytes(framed)


def _masked_crc(data: bytes) -> int:
    crc: int = google_crc32c.value(data)
    return (((crc >> 15) | (crc << 17)) + 0xA282EAD8) & 0xFFFFFFFF


def _field(number: int, payload: bytes) -> bytes:
    return _varint(number << 3 | _LENGTH_DELIMITED) + _varint(len(payload)) + payload


def _varint(value: int) -> bytes:
    encoded = bytearray()
    while True:
        bits = value & 0x7F
        value >>= 7
        if not value:
            encoded.append(bits)
            return bytes(encoded)
        encoded.append(bits | 0x80)
