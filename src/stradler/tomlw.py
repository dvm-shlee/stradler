"""Built-in TOML writer.

The writer covers the shapes the stradler schemas define: tables, strings,
integers, floats, booleans, arrays, inline tables and datetimes. Output is
read back with ``tomllib`` in the tests.
"""

from __future__ import annotations

import datetime as _dt
import math
import re
from typing import Any

__all__ = ["TomlWriteError", "dumps"]

_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_ESC = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


class TomlWriteError(ValueError):
    """Raised when a value cannot be written as TOML."""


def _key(k: Any) -> str:
    if not isinstance(k, str) or k == "":
        raise TomlWriteError(f"keys must be non-empty strings, got {k!r}")
    return k if _BARE.match(k) else _string(k)


def _string(s: str) -> str:
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _value(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        if math.isnan(v):
            return "nan"
        if math.isinf(v):
            return "inf" if v > 0 else "-inf"
        return repr(v)
    if isinstance(v, str):
        return _string(v)
    if isinstance(v, (_dt.datetime, _dt.date, _dt.time)):
        return v.isoformat()
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_value(x) for x in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        return "{ " + ", ".join(f"{_key(k)} = {_value(x)}" for k, x in v.items()) + " }"
    raise TomlWriteError(f"cannot write {type(v).__name__} as TOML")


def _is_table_array(v: Any) -> bool:
    return isinstance(v, (list, tuple)) and len(v) > 0 and all(isinstance(x, dict) for x in v)


def _emit(table: dict, path: list[str], lines: list[str]) -> None:
    for k, v in table.items():
        if not isinstance(v, dict) and not _is_table_array(v):
            lines.append(f"{_key(k)} = {_value(v)}")
    for k, v in table.items():
        if isinstance(v, dict):
            sub = path + [_key(k)]
            if lines:
                lines.append("")
            lines.append("[" + ".".join(sub) + "]")
            _emit(v, sub, lines)
        elif _is_table_array(v):
            sub = path + [_key(k)]
            for item in v:
                if lines:
                    lines.append("")
                lines.append("[[" + ".".join(sub) + "]]")
                _emit(item, sub, lines)


def dumps(data: dict) -> str:
    """Serialize a mapping to TOML text.

    Parameters
    ----------
    data : dict
        Mapping with string keys. Nested mappings become tables, lists of
        mappings become arrays of tables.

    Returns
    -------
    str
        TOML document ending in a newline.

    Raises
    ------
    TomlWriteError
        If a key or value has no TOML form (for example ``None``).
    """
    if not isinstance(data, dict):
        raise TomlWriteError("the document must be a mapping")
    lines: list[str] = []
    _emit(data, [], lines)
    return "\n".join(lines) + "\n" if lines else ""
