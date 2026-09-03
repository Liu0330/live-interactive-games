from __future__ import annotations


def decode_varint(data: bytes, i: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while i < len(data):
        byte = data[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not (byte & 0x80):
            return result, i
        shift += 7
        if shift > 70:
            break
    return result, i


def decode_fields(data: bytes) -> dict[int, list]:
    fields: dict[int, list] = {}
    i = 0
    n = len(data)
    while i < n:
        key, i = decode_varint(data, i)
        if i > n:
            break
        field_no = key >> 3
        wire = key & 7
        if wire == 0:
            val, i = decode_varint(data, i)
        elif wire == 1:
            val = data[i : i + 8]
            i += 8
        elif wire == 2:
            length, i = decode_varint(data, i)
            val = data[i : i + length]
            i += length
        elif wire == 5:
            val = data[i : i + 4]
            i += 4
        else:
            break
        fields.setdefault(field_no, []).append(val)
    return fields


def first_bytes(fields: dict[int, list], no: int) -> bytes | None:
    vals = fields.get(no) or []
    for item in vals:
        if isinstance(item, (bytes, bytearray)):
            return bytes(item)
    return None


def first_str(fields: dict[int, list], no: int) -> str:
    raw = first_bytes(fields, no)
    if raw is None:
        return ""
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def first_int(fields: dict[int, list], no: int) -> int:
    vals = fields.get(no) or []
    for item in vals:
        if isinstance(item, int):
            return item
    return 0
