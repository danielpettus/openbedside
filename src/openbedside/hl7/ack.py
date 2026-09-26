"""Acknowledgements. IHE Devices profiles use MSH-15 NE, MSH-16 AL: no transport-level
accept ACK, an application ACK always. The gateway treats a message as delivered
only when the receiver returns an application ACK of AA (or CA)."""
from __future__ import annotations

from .message import Message, msh, segment, control_id, escape

POSITIVE = {"AA", "CA"}
RETRYABLE = {"AE", "CE"}     # receiver hit an error; worth trying again later
REJECTED = {"AR", "CR"}      # receiver refuses this message; retrying will not help


def build_ack(incoming: Message, code: str, text: str = "", sending_app: str = "OpenBedside",
              sending_fac: str = "GATEWAY", err_code: str | None = None) -> str:
    receiving_app = incoming.get("MSH", 3)
    receiving_fac = incoming.get("MSH", 4)
    trigger = incoming.get("MSH", 9, 2)
    lines = [
        msh(sending_app, sending_fac, receiving_app, receiving_fac,
            f"ACK^{trigger}^ACK", control_id("AK"), incoming.get("MSH", 21),
            ack_accept="NE", ack_app="NE"),
        segment("MSA", code, incoming.control_id, escape(text)),
    ]
    if err_code:
        lines.append(segment("ERR", "", "", "", "E", err_code, "", "", escape(text)))
    return "\r".join(lines) + "\r"


def parse_ack(raw: str) -> tuple[str, str, str]:
    """Return (code, acknowledged control id, text)."""
    m = Message(raw)
    return m.get("MSA", 1), m.get("MSA", 2), m.get("MSA", 3)
