"""Strict YAML subset reader.

Accepted: block mappings, block sequences, flow sequences, plain and quoted
scalars, comments, and one leading ``---``. Rejected with a
:class:`YamlSubsetError`: anchors, aliases, tags, block scalars, flow
mappings, directives, tabs as indentation, duplicate keys and multi-document
streams. There is no implicit typing: every scalar is a string. Use
:func:`stradler.schema.coerce` to convert strings where a schema says so.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["YamlSubsetError", "loads"]


class YamlSubsetError(ValueError):
    """Raised for input outside the supported YAML subset."""


_DQ_ESC = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", '"': '"', "\\": "\\", "/": "/", " ": " "}
_FORBIDDEN_START = "&*!|>%@`"


def _strip_comment(line: str) -> str:
    quote = None
    i = 0
    while i < len(line):
        ch = line[i]
        if quote == '"':
            if ch == "\\":
                i += 1
            elif ch == '"':
                quote = None
        elif quote == "'":
            if ch == "'":
                quote = None
        else:
            if ch in "\"'" and (i == 0 or line[i - 1] in " \t[,:-"):
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                return line[:i].rstrip()
        i += 1
    return line.rstrip()


def _quoted(text: str, lineno: int) -> tuple[str, int]:
    """Parse a quoted scalar at text[0]; return (value, index after it)."""
    q = text[0]
    out = []
    i = 1
    while i < len(text):
        ch = text[i]
        if q == "'":
            if ch == "'":
                if text[i + 1 : i + 2] == "'":
                    out.append("'")
                    i += 2
                    continue
                return "".join(out), i + 1
            out.append(ch)
        else:
            if ch == "\\":
                nxt = text[i + 1 : i + 2]
                if nxt not in _DQ_ESC:
                    raise YamlSubsetError(f"line {lineno}: unsupported escape \\{nxt}")
                out.append(_DQ_ESC[nxt])
                i += 2
                continue
            if ch == '"':
                return "".join(out), i + 1
            out.append(ch)
        i += 1
    raise YamlSubsetError(f"line {lineno}: unterminated quoted scalar")


def _plain(text: str, lineno: int, in_flow: bool = False) -> str:
    text = text.strip()
    if text and text[0] in _FORBIDDEN_START:
        raise YamlSubsetError(f"line {lineno}: {text[0]!r} (anchor, alias, tag or block scalar) is not supported")
    if text.startswith("{"):
        raise YamlSubsetError(f"line {lineno}: flow mappings are not supported")
    if re.search(r":(\s|$)", text):
        raise YamlSubsetError(f"line {lineno}: ': ' inside a plain scalar; quote it")
    if in_flow and re.search(r"[\[\]{}]", text):
        raise YamlSubsetError(f"line {lineno}: bracket inside a plain flow scalar; quote it")
    return text


def _flow_seq(text: str, lineno: int) -> list:
    pos = 0

    def skip_ws() -> None:
        nonlocal pos
        while pos < len(text) and text[pos] in " \t":
            pos += 1

    def parse_seq() -> list:
        nonlocal pos
        pos += 1  # the '['
        items: list = []
        while True:
            skip_ws()
            if pos >= len(text):
                raise YamlSubsetError(f"line {lineno}: unterminated flow sequence")
            if text[pos] == "]":
                pos += 1
                return items
            items.append(parse_item())
            skip_ws()
            if pos < len(text) and text[pos] == ",":
                pos += 1
            elif pos < len(text) and text[pos] == "]":
                continue
            else:
                raise YamlSubsetError(f"line {lineno}: expected ',' or ']' in flow sequence")

    def parse_item() -> Any:
        nonlocal pos
        ch = text[pos]
        if ch == "[":
            return parse_seq()
        if ch in "\"'":
            val, n = _quoted(text[pos:], lineno)
            pos += n
            return val
        start = pos
        while pos < len(text) and text[pos] not in ",]":
            pos += 1
        return _plain(text[start:pos], lineno, in_flow=True)

    value = parse_seq()
    skip_ws()
    if pos != len(text):
        raise YamlSubsetError(f"line {lineno}: unexpected text after flow sequence")
    return value


def _scalar(text: str, lineno: int) -> Any:
    text = text.strip()
    if text.startswith("["):
        return _flow_seq(text, lineno)
    if text and text[0] in "\"'":
        val, n = _quoted(text, lineno)
        if text[n:].strip():
            raise YamlSubsetError(f"line {lineno}: unexpected text after quoted scalar")
        return val
    return _plain(text, lineno)


def _split_key(content: str, lineno: int) -> tuple[str, str]:
    if content[0] in "\"'":
        key, n = _quoted(content, lineno)
        rest = content[n:]
        if not rest.startswith(":") or (len(rest) > 1 and rest[1] not in " \t"):
            raise YamlSubsetError(f"line {lineno}: expected ':' after quoted key")
        return key, rest[1:].strip()
    m = re.match(r"^([^:]*?)\s*:(?:\s+(.*))?$", content)
    if not m:
        raise YamlSubsetError(f"line {lineno}: expected 'key: value'")
    key = _plain(m.group(1), lineno)
    if key == "":
        raise YamlSubsetError(f"line {lineno}: empty key")
    return key, (m.group(2) or "").strip()


def _is_mapping_line(content: str) -> bool:
    if content[0] in "\"'":
        try:
            _split_key(content, 0)
            return True
        except YamlSubsetError:
            return False
    if content.startswith("["):
        return False
    return re.search(r":(\s|$)", content) is not None


class _Parser:
    def __init__(self, text: str) -> None:
        self.lines: list[list] = []  # [indent, content, lineno]
        started = False
        for n, raw in enumerate(text.splitlines(), 1):
            if n == 1 and raw.startswith("﻿"):
                raw = raw[1:]
            stripped = _strip_comment(raw)
            if not stripped.strip():
                continue
            if stripped.startswith("%"):
                raise YamlSubsetError(f"line {n}: directives are not supported")
            if stripped.strip() == "---" and not started:
                started = True
                continue
            if stripped.startswith(("---", "...")) and stripped.strip()[3:4] in ("", " "):
                raise YamlSubsetError(f"line {n}: multi-document streams are not supported")
            started = True
            indent = len(stripped) - len(stripped.lstrip(" "))
            if stripped[indent : indent + 1] == "\t":
                raise YamlSubsetError(f"line {n}: tab indentation")
            self.lines.append([indent, stripped[indent:], n])
        self.i = 0

    def parse(self) -> Any:
        if not self.lines:
            return {}
        value = self.block(self.lines[0][0])
        if self.i < len(self.lines):
            raise YamlSubsetError(f"line {self.lines[self.i][2]}: unexpected indentation")
        return value

    @staticmethod
    def _is_seq(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def block(self, indent: int) -> Any:
        content = self.lines[self.i][1]
        if self._is_seq(content):
            return self.sequence(indent)
        if not _is_mapping_line(content):
            n = self.lines[self.i][2]
            self.i += 1
            if self.i < len(self.lines):
                raise YamlSubsetError(f"line {n}: a scalar document must be one line")
            return _scalar(content, n)
        return self.mapping(indent)

    def child(self, parent_indent: int, allow_same_seq: bool) -> Any:
        """Parse the nested block under a key or '-', or return '' if none."""
        if self.i < len(self.lines):
            ind, content, _ = self.lines[self.i]
            if ind > parent_indent:
                return self.block(ind)
            if allow_same_seq and ind == parent_indent and self._is_seq(content):
                return self.sequence(ind)
        return ""

    def mapping(self, indent: int) -> dict:
        result: dict = {}
        while self.i < len(self.lines):
            ind, content, n = self.lines[self.i]
            if ind < indent:
                break
            if ind > indent:
                raise YamlSubsetError(f"line {n}: unexpected indentation")
            if self._is_seq(content):
                raise YamlSubsetError(f"line {n}: sequence item inside a mapping")
            key, rest = _split_key(content, n)
            if key in result:
                raise YamlSubsetError(f"line {n}: duplicate key {key!r}")
            self.i += 1
            result[key] = _scalar(rest, n) if rest else self.child(indent, True)
        return result

    def sequence(self, indent: int) -> list:
        result: list = []
        while self.i < len(self.lines):
            ind, content, n = self.lines[self.i]
            if ind < indent:
                break
            if ind > indent:
                raise YamlSubsetError(f"line {n}: unexpected indentation")
            if not self._is_seq(content):
                break
            rest = content[1:].lstrip(" ")
            if not rest:
                self.i += 1
                result.append(self.child(indent, False))
            elif _is_mapping_line(rest):
                # "- key: value": the rest is a mapping line at a deeper indent
                offset = len(content) - len(rest)
                self.lines[self.i] = [indent + offset, rest, n]
                result.append(self.mapping(indent + offset))
            else:
                self.i += 1
                result.append(_scalar(rest, n))
        return result


def loads(text: str) -> Any:
    """Parse a document of the strict YAML subset.

    Parameters
    ----------
    text : str
        Document text.

    Returns
    -------
    dict or list
        Nested ``dict``, ``list`` and ``str`` values only. An empty document
        gives ``{}``; a key with no value gives ``""``.

    Raises
    ------
    YamlSubsetError
        For any construct outside the subset.
    """
    return _Parser(text).parse()
