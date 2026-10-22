"""
The DDS sample type, in a module of its own.

Cyclone DDS resolves IDL field annotations by name in the defining module's
globals, so the field types must be module-level imports and annotations must
not be postponed (no ``from __future__ import annotations`` here). Importing
this module requires ``cyclonedds``; `saag.testbed.dds` imports it lazily.
"""

from dataclasses import dataclass

from cyclonedds.idl import IdlStruct
from cyclonedds.idl.types import float64, int64


@dataclass
class Sample(IdlStruct, typename="saag::Sample"):
    msg_id: int64
    publisher: str
    created: float64
    size: int64
