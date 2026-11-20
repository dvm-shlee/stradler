"""Minimal JSON Schema validator and bundled schema access.

Implements the keywords the stradler schemas use: ``type``, ``const``,
``enum``, ``pattern``, ``minLength``, ``minProperties``, ``minItems``, ``minimum``, ``required``,
``properties``, ``additionalProperties``, ``propertyNames``, ``items``,
``uniqueItems``, ``allOf``, ``anyOf`` and ``not``. Annotation keywords
(``$schema``, ``$id``, ``title``, ``description``) are ignored. An
unsupported keyword raises :class:`SchemaError` rather than being skipped.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

__all__ = [
    "CORE_SCHEMAS",
    "SchemaError",
    "bundled_schema_dir",
    "coerce",
    "load_bundled",
    "schema_digest",
    "validate",
]

CORE_SCHEMAS = ("stradler", "config", "structure")
_ANNOTATIONS = {"$schema", "$id", "title", "description", "default", "examples"}
_KNOWN = {
    "type", "const", "enum", "pattern", "minLength", "minProperties", "minItems", "minimum", "required", "properties",
    "additionalProperties", "propertyNames", "items", "uniqueItems", "allOf", "anyOf", "not",
}


class SchemaError(ValueError):
    """Raised for a schema that uses an unsupported keyword or cannot be found."""


def bundled_schema_dir() -> Path:
    """Return the directory holding the core schema files.

    Returns
    -------
    pathlib.Path
        ``stradler/schemas`` inside an installed package, else the
        repository's ``schemas`` directory (development checkout).
    """
    here = Path(__file__).resolve().parent
    for cand in (here / "schemas", here.parent.parent / "schemas"):
        if cand.is_dir() and any(cand.glob("*@*.json")):
            return cand
    raise SchemaError("bundled schemas not found")


def load_bundled(name: str, version: str = "1.0.0") -> dict:
    """Load a core schema by name and version.

    Parameters
    ----------
    name : str
        One of ``stradler``, ``config``, ``structure``.
    version : str
        ``MAJOR.MINOR.PATCH``.

    Returns
    -------
    dict
        The parsed schema.
    """
    path = bundled_schema_dir() / f"{name}@{version}.json"
    if not path.is_file():
        raise SchemaError(f"no bundled schema {name}@{version}")
    return json.loads(path.read_text(encoding="utf-8"))


def schema_digest(path: Path) -> str:
    """Return ``sha256:<hex>`` of a schema file's bytes."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _type_ok(value: Any, t: str) -> bool:
    if t == "string":
        return isinstance(value, str)
    if t == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if t == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if t == "boolean":
        return isinstance(value, bool)
    if t == "object":
        return isinstance(value, dict)
    if t == "array":
        return isinstance(value, list)
    if t == "null":
        return value is None
    raise SchemaError(f"unsupported type {t!r}")


def _walk(value: Any, schema: dict, path: str, errors: list[str]) -> None:
    for k in schema:
        if k not in _KNOWN and k not in _ANNOTATIONS:
            raise SchemaError(f"unsupported schema keyword {k!r}")
    where = path or "<root>"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{where}: expected {'/'.join(types)}, got {type(value).__name__}")
            return
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{where}: shorter than {schema['minLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: {value!r} does not match {schema['pattern']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: {value!r} is less than {schema['minimum']}")
    if isinstance(value, dict):
        if "minProperties" in schema and len(value) < schema["minProperties"]:
            errors.append(f"{where}: needs at least {schema['minProperties']} entries")
        for r in schema.get("required", []):
            if r not in value:
                errors.append(f"{where}: missing required key {r!r}")
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for k, v in value.items():
            sub = f"{path}.{k}" if path else k
            if "propertyNames" in schema:
                _walk(k, schema["propertyNames"], f"{sub} (name)", errors)
            if k in props:
                _walk(v, props[k], sub, errors)
            elif extra is False:
                errors.append(f"{where}: unknown key {k!r}")
            elif isinstance(extra, dict):
                _walk(v, extra, sub, errors)
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{where}: needs at least {schema['minItems']} items")
        if "items" in schema:
            for i, v in enumerate(value):
                _walk(v, schema["items"], f"{path}[{i}]", errors)
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{where}: items must be unique")
    for sub in schema.get("allOf", []):
        _walk(value, sub, path, errors)
    if "anyOf" in schema and not any(_errors_of(value, s, path) == [] for s in schema["anyOf"]):
            errors.append(f"{where}: matches none of the allowed alternatives")
    if "not" in schema and _errors_of(value, schema["not"], path) == []:
        errors.append(f"{where}: matches a forbidden form")


def _errors_of(value: Any, schema: dict, path: str) -> list[str]:
    errs: list[str] = []
    _walk(value, schema, path, errs)
    return errs


def validate(instance: Any, schema: dict) -> list[str]:
    """Validate an instance against a schema.

    Parameters
    ----------
    instance : object
        Parsed JSON data model (dict, list, str, int, bool).
    schema : dict
        Schema using the supported keywords.

    Returns
    -------
    list of str
        One message per violation; empty when valid.
    """
    return _errors_of(instance, schema, "")


def coerce(data: Any, schema: dict) -> Any:
    """Convert strings to booleans or integers where the schema says so.

    The YAML subset reader returns only strings. This is the one place a
    string becomes another type, and only for exact ``true``/``false`` and
    decimal integers.

    Parameters
    ----------
    data : object
        Parsed document.
    schema : dict
        Schema describing the document.

    Returns
    -------
    object
        A new structure with converted scalars.
    """
    t = schema.get("type")
    if isinstance(data, str):
        if t == "boolean" and data in ("true", "false"):
            return data == "true"
        if t == "integer" and re.fullmatch(r"-?(0|[1-9][0-9]*)", data):
            return int(data)
        return data
    if isinstance(data, dict):
        props = schema.get("properties", {})
        extra = schema.get("additionalProperties")
        out = {}
        for k, v in data.items():
            sub = props.get(k, extra if isinstance(extra, dict) else {})
            out[k] = coerce(v, sub)
        return out
    if isinstance(data, list):
        return [coerce(v, schema.get("items", {})) for v in data]
    return data
