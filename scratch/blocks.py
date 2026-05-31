"""
Block definitions for the Scratch-like programming system.

Block types:
  HAT      – trigger/start (rounded top, puzzle-bottom)
  COMMAND  – imperative action (puzzle top+bottom)
  REPORTER – returns a value (rounded pill, no connectors)
  CONTROL  – flow control with C-shaped body (if/repeat)
  VALUE    – literal constant or variable reference
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Optional


class BlockType(Enum):
    HAT      = auto()
    COMMAND  = auto()
    REPORTER = auto()
    CONTROL  = auto()
    VALUE    = auto()


class BlockCategory(Enum):
    EVENTS      = auto()
    CONTROL     = auto()
    MEASUREMENT = auto()
    INSTRUMENT  = auto()
    DATA        = auto()
    OPERATORS   = auto()
    VARIABLES   = auto()
    LOGGING     = auto()


# Category display colours (matches Scratch palette style)
CATEGORY_COLORS: dict[BlockCategory, str] = {
    BlockCategory.EVENTS:      "#FFAB19",  # orange
    BlockCategory.CONTROL:     "#FFAB19",  # orange (lighter)
    BlockCategory.MEASUREMENT: "#5CB1D6",  # light blue
    BlockCategory.INSTRUMENT:  "#4C97FF",  # blue
    BlockCategory.DATA:        "#FF8C1A",  # dark orange
    BlockCategory.OPERATORS:   "#59C059",  # green
    BlockCategory.VARIABLES:   "#FF6680",  # pink
    BlockCategory.LOGGING:     "#9966FF",  # purple
}


@dataclass
class FieldDef:
    name: str
    label: str
    type: str = "text"        # text | number | select | bool
    default: Any = ""
    options: list[str] = field(default_factory=list)


@dataclass
class BlockDef:
    id: str
    label: str
    type: BlockType
    category: BlockCategory
    fields: list[FieldDef] = field(default_factory=list)
    description: str = ""

    @property
    def color(self) -> str:
        return CATEGORY_COLORS[self.category]


# ── Block library ────────────────────────────────────────────────────────────

BLOCK_LIBRARY: list[BlockDef] = [

    # ── Events ─────────────────────────────────────────────────────────────
    BlockDef("on_start",    "When ▶ clicked",   BlockType.HAT,     BlockCategory.EVENTS),
    BlockDef("on_timer",    "Every [interval] s", BlockType.HAT,   BlockCategory.EVENTS,
             fields=[FieldDef("interval", "Interval (s)", "number", "1.0")]),
    BlockDef("on_measure",  "On measurement",   BlockType.HAT,     BlockCategory.EVENTS),

    # ── Instrument ─────────────────────────────────────────────────────────
    BlockDef("set_mode",    "Set mode [mode]",  BlockType.COMMAND, BlockCategory.INSTRUMENT,
             fields=[FieldDef("mode", "Mode", "select", "Cs",
                              ["Ls","Lp","Cs","Cp","Rs","Rp","Z","D","Q","Theta","ESR","DCR"])]),
    BlockDef("set_freq",    "Set frequency [freq]", BlockType.COMMAND, BlockCategory.INSTRUMENT,
             fields=[FieldDef("freq", "Frequency", "select", "1 kHz",
                              ["100 Hz","120 Hz","1 kHz","10 kHz","100 kHz"])]),
    BlockDef("set_hold",    "Hold [state]",     BlockType.COMMAND, BlockCategory.INSTRUMENT,
             fields=[FieldDef("state", "State", "select", "ON", ["ON","OFF"])]),
    BlockDef("set_rel",     "REL [state]",      BlockType.COMMAND, BlockCategory.INSTRUMENT,
             fields=[FieldDef("state", "State", "select", "ON", ["ON","OFF"])]),
    BlockDef("beep",        "Beep",             BlockType.COMMAND, BlockCategory.INSTRUMENT),

    # ── Measurement (reporters) ────────────────────────────────────────────
    BlockDef("read_primary",   "primary value",   BlockType.REPORTER, BlockCategory.MEASUREMENT),
    BlockDef("read_secondary", "secondary value", BlockType.REPORTER, BlockCategory.MEASUREMENT),
    BlockDef("read_phase",     "phase angle °",   BlockType.REPORTER, BlockCategory.MEASUREMENT),
    BlockDef("read_mode",      "current mode",    BlockType.REPORTER, BlockCategory.MEASUREMENT),
    BlockDef("read_freq",      "test frequency",  BlockType.REPORTER, BlockCategory.MEASUREMENT),
    BlockDef("is_overload",    "overload?",        BlockType.REPORTER, BlockCategory.MEASUREMENT),

    # ── Control ────────────────────────────────────────────────────────────
    BlockDef("wait",        "Wait [secs] s",    BlockType.COMMAND, BlockCategory.CONTROL,
             fields=[FieldDef("secs", "Seconds", "number", "1")]),
    BlockDef("repeat",      "Repeat [times]",   BlockType.CONTROL, BlockCategory.CONTROL,
             fields=[FieldDef("times", "Times", "number", "10")]),
    BlockDef("repeat_forever", "Repeat forever", BlockType.CONTROL, BlockCategory.CONTROL),
    BlockDef("if_block",    "If [condition]",   BlockType.CONTROL, BlockCategory.CONTROL,
             fields=[FieldDef("condition", "Condition", "text", "")]),
    BlockDef("stop_all",    "Stop all",         BlockType.COMMAND, BlockCategory.CONTROL),

    # ── Variables ──────────────────────────────────────────────────────────
    BlockDef("set_var",     "Set [var] to [value]", BlockType.COMMAND, BlockCategory.VARIABLES,
             fields=[FieldDef("var",   "Variable", "text",   "myVar"),
                     FieldDef("value", "Value",    "text",   "0")]),
    BlockDef("change_var",  "Change [var] by [delta]", BlockType.COMMAND, BlockCategory.VARIABLES,
             fields=[FieldDef("var",   "Variable", "text",   "myVar"),
                     FieldDef("delta", "Delta",    "number", "1")]),
    BlockDef("get_var",     "[var]",             BlockType.REPORTER, BlockCategory.VARIABLES,
             fields=[FieldDef("var", "Variable", "text", "myVar")]),

    # ── Operators ──────────────────────────────────────────────────────────
    BlockDef("op_add",  "[a] + [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","0"), FieldDef("b","B","number","0")]),
    BlockDef("op_sub",  "[a] − [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","0"), FieldDef("b","B","number","0")]),
    BlockDef("op_mul",  "[a] × [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","1"), FieldDef("b","B","number","1")]),
    BlockDef("op_div",  "[a] ÷ [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","1"), FieldDef("b","B","number","1")]),
    BlockDef("op_gt",   "[a] > [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","0"), FieldDef("b","B","number","0")]),
    BlockDef("op_lt",   "[a] < [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","number","0"), FieldDef("b","B","number","0")]),
    BlockDef("op_eq",   "[a] = [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","text",""), FieldDef("b","B","text","")]),
    BlockDef("op_and",  "[a] AND [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","bool",""), FieldDef("b","B","bool","")]),
    BlockDef("op_or",   "[a] OR [b]", BlockType.REPORTER, BlockCategory.OPERATORS,
             fields=[FieldDef("a","A","bool",""), FieldDef("b","B","bool","")]),

    # ── Logging ────────────────────────────────────────────────────────────
    BlockDef("log_measurement", "Log measurement to CSV", BlockType.COMMAND, BlockCategory.LOGGING),
    BlockDef("log_value", "Log [label] = [value]", BlockType.COMMAND, BlockCategory.LOGGING,
             fields=[FieldDef("label","Label","text","value"),
                     FieldDef("value","Value","text","0")]),
    BlockDef("show_value","Show [label] = [value]", BlockType.COMMAND, BlockCategory.LOGGING,
             fields=[FieldDef("label","Label","text","value"),
                     FieldDef("value","Value","text","0")]),
    BlockDef("start_log","Start CSV log [file]", BlockType.COMMAND, BlockCategory.LOGGING,
             fields=[FieldDef("file","File","text","log.csv")]),
    BlockDef("stop_log", "Stop CSV log",         BlockType.COMMAND, BlockCategory.LOGGING),
]

BLOCK_BY_ID: dict[str, BlockDef] = {b.id: b for b in BLOCK_LIBRARY}


# ── Runtime instances ────────────────────────────────────────────────────────

@dataclass
class BlockInstance:
    block_id: str                               # references BlockDef.id
    uid: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    field_values: dict[str, Any] = field(default_factory=dict)
    children: list["BlockInstance"] = field(default_factory=list)  # body blocks
    next: Optional["BlockInstance"] = None      # next block in sequence
    x: float = 0.0
    y: float = 0.0

    @property
    def definition(self) -> BlockDef:
        return BLOCK_BY_ID[self.block_id]

    def get_field(self, name: str) -> Any:
        defn = self.definition
        for fd in defn.fields:
            if fd.name == name:
                return self.field_values.get(name, fd.default)
        return None

    def to_dict(self) -> dict:
        return {
            "block_id": self.block_id,
            "uid": self.uid,
            "field_values": self.field_values,
            "children": [c.to_dict() for c in self.children],
            "next": self.next.to_dict() if self.next else None,
            "x": self.x,
            "y": self.y,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BlockInstance":
        inst = cls(
            block_id=d["block_id"],
            uid=d.get("uid", str(uuid.uuid4())[:8]),
            field_values=d.get("field_values", {}),
            x=d.get("x", 0.0),
            y=d.get("y", 0.0),
        )
        inst.children = [cls.from_dict(c) for c in d.get("children", [])]
        inst.next = cls.from_dict(d["next"]) if d.get("next") else None
        return inst


@dataclass
class BlockProgram:
    """Top-level container: a list of hat stacks."""
    stacks: list[BlockInstance] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"stacks": [s.to_dict() for s in self.stacks]}

    @classmethod
    def from_dict(cls, d: dict) -> "BlockProgram":
        prog = cls()
        prog.stacks = [BlockInstance.from_dict(s) for s in d.get("stacks", [])]
        return prog
