"""Canonical incremental encoding and private, lifetime-scoped report sections."""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import tempfile
from pydantic import BaseModel


def chunks(value, *, exclude=(), overrides=None):
    """Match JSON-mode Pydantic primitives without constructing an object copy."""
    if isinstance(value, BaseModel):
        values = overrides or {}
        keys = sorted(k for k in type(value).model_fields if k not in exclude)
        yield "{"
        for i, key in enumerate(keys):
            if i: yield ","
            yield json.dumps(key, ensure_ascii=True) + ":"
            yield from chunks(values[key] if key in values else getattr(value, key))
        yield "}"
    elif isinstance(value, Mapping):
        if any(type(k) is not str for k in value): raise ValueError("CANONICAL_STRING_KEYS_REQUIRED")
        yield "{"
        for i, key in enumerate(sorted(value)):
            if i: yield ","
            yield json.dumps(key, ensure_ascii=True) + ":"
            yield from chunks(value[key])
        yield "}"
    elif isinstance(value, (tuple, list, ModelSpool)):
        yield "["
        for i, item in enumerate(value):
            if i: yield ","
            yield from chunks(item)
        yield "]"
    else:
        if isinstance(value, Decimal):
            if not value.is_finite(): raise ValueError("NONFINITE_CANONICAL_VALUE")
            value = str(value)
        elif isinstance(value, datetime):
            value = value.isoformat()
            if value.endswith("+00:00"): value = value[:-6] + "Z"
        elif isinstance(value, date): value = value.isoformat()
        elif value is not None and type(value) not in (str, int, bool, float):
            raise ValueError("UNSUPPORTED_CANONICAL_VALUE")
        yield json.dumps(value, ensure_ascii=True, separators=(",", ":"), allow_nan=False)


def digest(value, *, exclude=("audit_sha256",), overrides=None):
    sha = hashlib.sha256()
    for part in chunks(value, exclude=exclude, overrides=overrides): sha.update(part.encode("ascii"))
    return sha.hexdigest()


def write_json(value, stream):
    for part in chunks(value): stream.write(part)
    stream.write("\n")


class ModelSpool:
    """Append one validated model at a time; no persistent artifact or raw payload."""
    def __init__(self, model):
        self.model = model
        self.file = tempfile.TemporaryFile(mode="w+b")
        self.offsets = []

    def append(self, value):
        self.file.seek(0, 2)
        self.offsets.append(self.file.tell())
        for part in chunks(value): self.file.write(part.encode("ascii"))
        self.file.write(b"\n")

    def __len__(self): return len(self.offsets)

    def __getitem__(self, index):
        self.file.seek(self.offsets[index])
        return self.model.model_validate_json(self.file.readline())

    def __iter__(self):
        for i in range(len(self)): yield self[i]

    def __enter__(self): return self
    def __exit__(self, *args): self.file.close()


@dataclass(frozen=True, slots=True)
class SourceDateLink:
    as_of_date: date
    decision_only_intersection_count: int
    task268_status: str


@dataclass(frozen=True, slots=True)
class RcaLinkage:
    status: str
    audit_sha256: str
    per_date: tuple[SourceDateLink, ...] = ()
