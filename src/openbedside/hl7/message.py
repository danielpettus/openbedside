"""A deliberately small HL7 v2 layer: build segments, escape text, parse enough to
route and acknowledge. No external library, so the install stays one command.
"""
from __future__ import annotations

import itertools
import threading
from datetime import datetime, timezone

FIELD = "|"
COMPONENT = "^"
REPEAT = "~"
ESCAPE = "\\"
SUBCOMPONENT = "&"
ENCODING = "^~\\&"
SEG_END = "\r"

_ESC = [("\\", "\\E\\"), ("|", "\\F\\"), ("^", "\\S\\"), ("~", "\\R\\"), ("&", "\\T\\")]


def escape(text: str) -> str:
    """Escape a text value for use inside a field. Backslash must go first."""
    out = str(text)
    for raw, esc in _ESC:
        out = out.replace(raw, esc)
    return out.replace("\r", "\\X0D\\").replace("\n", "\\X0A\\")


def unescape(text: str) -> str:
    out = text.replace("\\X0D\\", "\r").replace("\\X0A\\", "\n")
    for raw, esc in reversed(_ESC):
        out = out.replace(esc, raw)
    return out


def hl7_ts(dt: datetime | None = None) -> str:
    """HL7 DTM with explicit UTC offset. A timestamp without an offset is ambiguous,
    and event ordering across devices depends on it."""
    dt = dt or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        raise ValueError("naive datetime refused: give it a time zone")
    return dt.strftime("%Y%m%d%H%M%S%z")


_counter = itertools.count(1)
_lock = threading.Lock()


def control_id(prefix: str = "OB") -> str:
    with _lock:
        n = next(_counter)
    return f"{prefix}{datetime.now(timezone.utc):%Y%m%d%H%M%S}{n:05d}"


def segment(name: str, *fields: str | None) -> str:
    return FIELD.join([name] + ["" if f is None else str(f) for f in fields])


def msh(sending_app: str, sending_fac: str, receiving_app: str, receiving_fac: str,
        msg_type: str, ctrl_id: str, profile: str, ack_accept: str = "NE",
        ack_app: str = "AL", processing_id: str = "P", when: datetime | None = None) -> str:
    # MSH-1 is the field separator itself, so MSH-2 is the first joined value.
    return FIELD.join(["MSH", ENCODING, sending_app, sending_fac, receiving_app, receiving_fac,
                       hl7_ts(when), "", msg_type, ctrl_id, processing_id, "2.6",
                       "", "", ack_accept, ack_app, "", "", "", "", profile])


class Message:
    """Parsed HL7 v2 message. Fields are 1-based as in the standard; MSH is adjusted
    so MSH-9 is get('MSH', 9) just as a reader of the spec would expect."""

    def __init__(self, raw: str):
        text = raw.replace("\n", "\r").strip("\r\x0b\x1c ")
        self.segments = [s for s in text.split("\r") if s]
        if not self.segments or not self.segments[0].startswith("MSH"):
            raise ValueError("not an HL7 v2 message: first segment is not MSH")

    def _fields(self, seg: str) -> list[str]:
        parts = seg.split(FIELD)
        if parts[0] == "MSH":
            return ["MSH", FIELD] + parts[1:]
        return parts

    def all(self, name: str) -> list[list[str]]:
        return [self._fields(s) for s in self.segments if s.split(FIELD, 1)[0] == name]

    def get(self, name: str, field_no: int, component: int | None = None, index: int = 0) -> str:
        segs = self.all(name)
        if index >= len(segs):
            return ""
        f = segs[index]
        if field_no >= len(f):
            return ""
        val = f[field_no]
        if component is None:
            return val
        comps = val.split(COMPONENT)
        return comps[component - 1] if component - 1 < len(comps) else ""

    @property
    def type(self) -> str:
        return self.get("MSH", 9)

    @property
    def control_id(self) -> str:
        return self.get("MSH", 10)

    def __str__(self) -> str:
        return SEG_END.join(self.segments) + SEG_END
