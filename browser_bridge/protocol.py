"""Framing native messaging: uint32 nativo + JSON UTF-8, limite de 1 MiB."""

import json
import struct

MAX_BYTES = 1024 * 1024


def encode(value: dict) -> bytes:
    if not isinstance(value, dict):
        raise ValueError("mensagem deve ser objeto")
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
    if not 0 < len(payload) <= MAX_BYTES:
        raise ValueError("mensagem fora do limite")
    return struct.pack("=I", len(payload)) + payload


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        chunk = stream.read(size - len(data))
        if not chunk:
            if not data:
                return None
            raise ValueError("frame incompleto")
        data.extend(chunk)
    return bytes(data)


def read(stream) -> dict | None:
    header = read_exact(stream, 4)
    if header is None:
        return None
    length = struct.unpack("=I", header)[0]
    if not 0 < length <= MAX_BYTES:
        raise ValueError("tamanho de frame recusado")
    payload = read_exact(stream, length)
    if payload is None:
        raise ValueError("payload ausente")

    def invalid_constant(_):
        raise ValueError("número JSON não finito")

    value = json.loads(payload.decode("utf-8"), parse_constant=invalid_constant)
    if not isinstance(value, dict):
        raise ValueError("mensagem deve ser objeto")
    return value


def write(stream, value):
    stream.write(encode(value))
    stream.flush()
