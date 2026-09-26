"""The device registry, the ADT census, manual associations and gateway settings.

All in the same SQLite file as the outbox. Three jobs:

  * Registration. A device is known to the gateway by vendor plus device id (two
    manufacturers can both ship serial 12345). Its entry says what kind of device it
    is, which identifier the hospital scans and files by (serial, asset tag or
    EUI-64), where it lives, and which ways of finding its patient are allowed.
    Unregistered devices are held, not sent, unless the configuration says otherwise.
  * Census. What the hospital's ADT feed says: which patient is in which bed.
    Identifiers and location only. Names and demographics are not stored.
  * Manual associations. A person tying a device to a patient on the Patients page.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field, fields
from typing import Optional

DEVICE_TYPES = ("infusion_pump", "ventilator", "other")
EHR_ID_CHOICES = ("serial", "asset", "eui64")
STATUSES = ("active", "retired")

SCHEMA = """
CREATE TABLE IF NOT EXISTS devices (
  vendor TEXT NOT NULL, device_id TEXT NOT NULL,
  device_type TEXT NOT NULL DEFAULT 'infusion_pump', model TEXT DEFAULT '',
  asset_tag TEXT DEFAULT '', eui64 TEXT DEFAULT '', ehr_id_from TEXT NOT NULL DEFAULT 'serial',
  unit TEXT DEFAULT '', room TEXT DEFAULT '', bed TEXT DEFAULT '', facility TEXT DEFAULT '',
  assoc_device INTEGER NOT NULL DEFAULT 1, assoc_adt INTEGER NOT NULL DEFAULT 0,
  assoc_manual INTEGER NOT NULL DEFAULT 1,
  binding TEXT DEFAULT '', status TEXT NOT NULL DEFAULT 'active', notes TEXT DEFAULT '',
  created REAL, updated REAL,
  PRIMARY KEY (vendor, device_id)
);
CREATE TABLE IF NOT EXISTS census (
  patient_id TEXT NOT NULL, authority TEXT NOT NULL DEFAULT '',
  visit TEXT DEFAULT '', patient_class TEXT DEFAULT '',
  unit TEXT DEFAULT '', room TEXT DEFAULT '', bed TEXT DEFAULT '', facility TEXT DEFAULT '',
  last_event TEXT DEFAULT '', updated REAL,
  PRIMARY KEY (patient_id, authority)
);
CREATE TABLE IF NOT EXISTS manual_assoc (
  device_key TEXT PRIMARY KEY, patient_id TEXT NOT NULL, authority TEXT DEFAULT '',
  set_at REAL, set_by TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (k TEXT PRIMARY KEY, v TEXT);
"""


@dataclass
class DeviceEntry:
    vendor: str
    device_id: str
    device_type: str = "infusion_pump"
    model: str = ""
    asset_tag: str = ""
    eui64: str = ""
    ehr_id_from: str = "serial"
    unit: str = ""
    room: str = ""
    bed: str = ""
    facility: str = ""
    assoc_device: bool = True          # accept a patient id reported by the device itself
    assoc_adt: bool = False            # accept the ADT census patient in this device's bed
    assoc_manual: bool = True          # accept an association made on the Patients page
    binding: str = ""                  # for devices that never send an id: adapter:port
    status: str = "active"
    notes: str = ""
    created: float = 0.0
    updated: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.vendor}|{self.device_id}"

    def ehr_id(self) -> str:
        """The identifier the hospital scans at the bedside and files device data by."""
        return {"asset": self.asset_tag, "eui64": self.eui64}.get(self.ehr_id_from) or self.device_id

    def validate(self) -> list[str]:
        problems = []
        if not self.vendor.strip():
            problems.append("vendor is required")
        if not self.device_id.strip():
            problems.append("device id (serial) is required")
        if self.device_type not in DEVICE_TYPES:
            problems.append(f"device type must be one of {', '.join(DEVICE_TYPES)}")
        if self.ehr_id_from not in EHR_ID_CHOICES:
            problems.append("send-to-EHR identifier must be serial, asset or eui64")
        if self.eui64 and not re.fullmatch(r"[0-9A-Fa-f]{16}", self.eui64):
            problems.append("EUI-64 must be 16 hexadecimal characters")
        if self.ehr_id_from == "asset" and not self.asset_tag.strip():
            problems.append("asset tag is required when the EHR identifier is the asset tag")
        if self.ehr_id_from == "eui64" and not self.eui64:
            problems.append("EUI-64 is required when the EHR identifier is the EUI-64")
        if self.assoc_adt and not (self.unit.strip() and self.bed.strip()):
            problems.append("association by ADT location needs at least a unit and a bed")
        if self.status not in STATUSES:
            problems.append("status must be active or retired")
        for f in ("vendor", "device_id", "asset_tag", "unit", "room", "bed", "facility"):
            if any(c in getattr(self, f) for c in "|^~\\&\r\n"):
                problems.append(f"{f} may not contain | ^ ~ \\ & or line breaks")
        return problems


@dataclass
class CensusEntry:
    patient_id: str
    authority: str = ""
    visit: str = ""
    patient_class: str = ""
    unit: str = ""
    room: str = ""
    bed: str = ""
    facility: str = ""
    last_event: str = ""
    updated: float = 0.0


@dataclass
class ManualAssoc:
    device_key: str
    patient_id: str
    authority: str = ""
    set_at: float = 0.0
    set_by: str = ""


def _norm(s: str) -> str:
    return (s or "").strip().upper()


class Registry:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA)
        self.version = 0                  # bumped on every change; the engine re-checks devices

    def _changed(self) -> None:
        self.version += 1

    # ---- devices --------------------------------------------------------------
    def save_device(self, e: DeviceEntry) -> list[str]:
        e.vendor, e.device_id = e.vendor.strip(), e.device_id.strip()
        e.eui64 = e.eui64.strip().upper()
        problems = e.validate()
        if problems:
            return problems
        now = time.time()
        old = self.get(e.vendor, e.device_id)
        e.created = old.created if old else now
        e.updated = now
        d = asdict(e)
        cols = ", ".join(d)
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO devices({cols}) VALUES ({', '.join('?' * len(d))})",
                             [int(v) if isinstance(v, bool) else v for v in d.values()])
        self._changed()
        return []

    def _entry(self, row) -> DeviceEntry:
        names = [f.name for f in fields(DeviceEntry)]
        e = DeviceEntry(**dict(zip(names, row)))
        e.assoc_device, e.assoc_adt, e.assoc_manual = bool(e.assoc_device), bool(e.assoc_adt), bool(e.assoc_manual)
        return e

    def get(self, vendor: str, device_id: str) -> Optional[DeviceEntry]:
        names = ", ".join(f.name for f in fields(DeviceEntry))
        with self._lock:
            row = self._db.execute(f"SELECT {names} FROM devices WHERE vendor=? AND device_id=?",
                                   (vendor, device_id)).fetchone()
        return self._entry(row) if row else None

    def devices(self) -> list[DeviceEntry]:
        names = ", ".join(f.name for f in fields(DeviceEntry))
        with self._lock:
            rows = self._db.execute(f"SELECT {names} FROM devices ORDER BY vendor, device_id").fetchall()
        return [self._entry(r) for r in rows]

    def set_status(self, vendor: str, device_id: str, status: str) -> bool:
        if status not in STATUSES:
            return False
        with self._lock:
            n = self._db.execute("UPDATE devices SET status=?, updated=? WHERE vendor=? AND device_id=?",
                                 (status, time.time(), vendor, device_id)).rowcount
        self._changed()
        return n == 1

    def device_for_binding(self, binding: str) -> Optional[DeviceEntry]:
        """For devices that never send their own id: the adapter asks which registered
        device is on this connection (for example 'terminal-server-3:port-7')."""
        for e in self.devices():
            if e.binding and e.binding == binding and e.status == "active":
                return e
        return None

    # ---- census (from the ADT feed) --------------------------------------------
    def census_upsert(self, c: CensusEntry) -> None:
        c.updated = time.time()
        d = asdict(c)
        with self._lock:
            self._db.execute(f"INSERT OR REPLACE INTO census({', '.join(d)}) VALUES ({', '.join('?' * len(d))})",
                             list(d.values()))
        self._changed()

    def census_remove(self, patient_id: str, authority: str = "") -> int:
        with self._lock:
            if authority:
                n = self._db.execute("DELETE FROM census WHERE patient_id=? AND authority=?",
                                     (patient_id, authority)).rowcount
            else:
                n = self._db.execute("DELETE FROM census WHERE patient_id=?", (patient_id,)).rowcount
        self._changed()
        return n

    def census(self) -> list[CensusEntry]:
        names = ", ".join(f.name for f in fields(CensusEntry))
        with self._lock:
            rows = self._db.execute(f"SELECT {names} FROM census ORDER BY unit, room, bed").fetchall()
        return [CensusEntry(*r) for r in rows]

    def census_find(self, patient_id: str, authority: str = "") -> Optional[CensusEntry]:
        for c in self.census():
            if c.patient_id == patient_id and (not authority or not c.authority or c.authority == authority):
                return c
        return None

    def patients_at(self, unit: str, room: str, bed: str) -> list[CensusEntry]:
        """Everyone the ADT feed places in this bed. Matching ignores case and spaces;
        an empty room on the device entry matches any room with that unit and bed."""
        out = []
        for c in self.census():
            if _norm(c.unit) == _norm(unit) and _norm(c.bed) == _norm(bed) and (not room or _norm(c.room) == _norm(room)):
                out.append(c)
        return out

    # ---- manual associations ----------------------------------------------------
    def set_manual(self, device_key: str, patient_id: str, authority: str = "", set_by: str = "") -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO manual_assoc VALUES (?,?,?,?,?)",
                             (device_key, patient_id.strip(), authority.strip(), time.time(), set_by))
        self._changed()

    def clear_manual(self, device_key: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM manual_assoc WHERE device_key=?", (device_key,))
        self._changed()

    def get_manual(self, device_key: str) -> Optional[ManualAssoc]:
        with self._lock:
            row = self._db.execute("SELECT device_key, patient_id, authority, set_at, set_by FROM manual_assoc "
                                   "WHERE device_key=?", (device_key,)).fetchone()
        return ManualAssoc(*row) if row else None

    # ---- settings and the admin password -------------------------------------------
    def setting(self, k: str) -> Optional[str]:
        with self._lock:
            row = self._db.execute("SELECT v FROM settings WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_setting(self, k: str, v: str) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (k, v))

    def set_admin_password(self, password: str) -> None:
        if len(password) < 10:
            raise ValueError("use at least 10 characters")
        salt = os.urandom(16)
        n = 390_000
        h = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, n)
        self.set_setting("admin_hash", f"pbkdf2_sha256${n}${salt.hex()}${h.hex()}")

    def has_admin_password(self) -> bool:
        return bool(self.setting("admin_hash"))

    def check_admin_password(self, password: str) -> bool:
        stored = self.setting("admin_hash")
        if not stored:
            return False
        try:
            _, n, salt, h = stored.split("$")
            got = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(n))
        except ValueError:
            return False
        return hmac.compare_digest(got.hex(), h)
