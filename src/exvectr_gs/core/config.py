"""Configuration loading and validation (gs.toml, design section 2.5). Unknown keys are errors."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

RC_ROLES = ("control", "mirror", "none")
SOURCE_KINDS = ("vehicle_usb", "link_module", "fake")


class ConfigError(ValueError):
    pass


@dataclass
class CoreSection:
    wire_profile: str = "provisional-1"
    source_priority: list[str] = field(default_factory=lambda: ["usb", "link", "fake"])
    stale_min_s: float = 0.5


@dataclass
class ServerSection:
    bind: str = "0.0.0.0"
    port: int = 8765
    control_hosts: list[str] = field(default_factory=lambda: ["127.0.0.1", "::1"])


@dataclass
class RecorderSection:
    directory: str = "~/exvectr-recordings"
    raw_frames: str = "unknown"  # only undecodable frames are kept; "all" is not offered (decision 10.1-2)
    flush_interval_ms: int = 100
    fsync_interval_ms: int = 1000


@dataclass
class SourceConfig:
    id: str
    kind: str
    match: dict[str, str] = field(default_factory=dict)
    baud: int | None = None
    rc_role: str = "none"
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)  # kind-specific keys (fake: profile, seed, ...)


@dataclass
class RadioSection:
    config: str = "config/radio.pocket.toml"


@dataclass
class GuiSection:
    layout: str = "config/layout.default.json"
    model: str = "config/model.placeholder.json"


REQUEST_CONFIRM_LEVELS = ("hold", "danger")
REQUEST_TARGETS = (1, 2, 3)  # 1 vehicle, 2 Router (Pocket module), 3 ship radio node (ExVectrNetwork node ids)


@dataclass(frozen=True)
class RequestEntry:
    """One whitelisted REQUEST the GUI and CLI may send (docs/requests.md). The ground station does not
    interpret the action: `label` and `group` are for display, `target` is the destination node and
    `confirm` the deliberate-confirmation level (`hold`, or `danger` for actions that stop a link)."""

    action: str
    target: int
    label: str
    group: str
    confirm: str = "hold"
    warning: str = ""  # shown in the `danger` modal and the CLI refusal (empty: the generic link-stop text)


@dataclass
class RequestsSection:
    """`[requests]`: the only REQUESTs the ground station will ever send. Empty = none."""

    # Source ids tried in this order (a source must be connected and able to reach the target). Not set:
    # every vehicle_usb source, then every link_module source, then fake sources, each in config order.
    source_preference: list[str] | None = None
    actions: list[RequestEntry] = field(default_factory=list)


TELECOMMAND_CONFIRM_LEVELS = ("hold",)
TELECOMMAND_SUBSYSTEMS = range(0, 7)  # flight software `Subsystem`: 0 gyro, 1 accel, 2 magneto, 3 baro, 4 gnss, ...


@dataclass(frozen=True)
class TelecommandEntry:
    """One whitelisted legacy telecommand (TEMPORARY, docs/telecommands.md). `type` is the flight software's
    `TelecommandType` name. `param_int` is the fixed int16 parameter, or the default of a client-settable one
    when `param_min`/`param_max` are given (then a client may send any value within [param_min, param_max]).
    The ground station does not interpret the command."""

    name: str
    type: str
    label: str
    group: str
    subsystem: int = 0
    param_int: int = 0
    param_min: int | None = None
    param_max: int | None = None
    confirm: str = "hold"
    note: str = ""  # shown next to the button (for example "toggles on the vehicle")


@dataclass
class TelecommandsSection:
    """`[telecommands]`: the only legacy telecommands the ground station will ever send. Empty = none."""

    source_preference: list[str] | None = None
    commands: list[TelecommandEntry] = field(default_factory=list)


@dataclass
class GsConfig:
    core: CoreSection = field(default_factory=CoreSection)
    server: ServerSection = field(default_factory=ServerSection)
    recorder: RecorderSection = field(default_factory=RecorderSection)
    sources: list[SourceConfig] = field(default_factory=list)
    radio: RadioSection = field(default_factory=RadioSection)
    gui: GuiSection = field(default_factory=GuiSection)
    requests: RequestsSection = field(default_factory=RequestsSection)
    telecommands: TelecommandsSection = field(default_factory=TelecommandsSection)


def _section(cls: type, data: dict, where: str):
    unknown = set(data) - set(cls.__dataclass_fields__)
    if unknown:
        raise ConfigError(f"[{where}]: unknown key(s) {sorted(unknown)}")
    return cls(**data)


def _source(data: dict) -> SourceConfig:
    data = dict(data)
    known = {k: data.pop(k) for k in ("id", "kind", "match", "baud", "rc_role", "enabled") if k in data}
    if "id" not in known or "kind" not in known:
        raise ConfigError("[[source]]: 'id' and 'kind' are required")
    return SourceConfig(**known, options=data)


def _requests(data: dict) -> RequestsSection:
    from ..packets.defs.link import RequestAction

    data = dict(data)
    pref = data.pop("source_preference", None)
    raw = data.pop("action", [])
    if data:
        raise ConfigError(f"[requests]: unknown key(s) {sorted(data)}")
    if pref is not None and not (isinstance(pref, list) and all(isinstance(x, str) for x in pref)):
        raise ConfigError("[requests] source_preference must be a list of source ids")
    sec = RequestsSection(source_preference=pref)
    if not isinstance(raw, list):
        raise ConfigError("[[requests.action]] must be an array of tables")
    seen: set[tuple[str, int]] = set()
    for i, d in enumerate(raw):
        where = f"[[requests.action]] #{i + 1}"
        unknown = set(d) - {"action", "label", "group", "target", "confirm", "warning"}
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        name = d.get("action")
        if name == "NONE" or name not in RequestAction.__members__:
            raise ConfigError(f"{where}: unknown action {name!r}")
        target = d.get("target")
        if isinstance(target, bool) or target not in REQUEST_TARGETS:
            raise ConfigError(f"{where} {name}: target must be one of {REQUEST_TARGETS}")
        confirm = d.get("confirm", "hold")
        if confirm not in REQUEST_CONFIRM_LEVELS:
            raise ConfigError(f"{where} {name}: confirm must be one of {REQUEST_CONFIRM_LEVELS}")
        if (name, target) in seen:
            raise ConfigError(f"{where}: {name} for target {target} is listed twice")
        seen.add((name, target))
        label, group = d.get("label", name), d.get("group", "Other")
        if not isinstance(label, str) or not isinstance(group, str) or not label or not group:
            raise ConfigError(f"{where} {name}: label and group must be non-empty strings")
        warning = d.get("warning", "")
        if not isinstance(warning, str):
            raise ConfigError(f"{where} {name}: warning must be a string")
        sec.actions.append(RequestEntry(name, target, label, group, confirm, warning))
    return sec


def _telecommands(data: dict) -> TelecommandsSection:
    from ..packets.defs.link import TelecommandType

    data = dict(data)
    pref = data.pop("source_preference", None)
    raw = data.pop("command", [])
    if data:
        raise ConfigError(f"[telecommands]: unknown key(s) {sorted(data)}")
    if pref is not None and not (isinstance(pref, list) and all(isinstance(x, str) for x in pref)):
        raise ConfigError("[telecommands] source_preference must be a list of source ids")
    sec = TelecommandsSection(source_preference=pref)
    if not isinstance(raw, list):
        raise ConfigError("[[telecommands.command]] must be an array of tables")
    seen: set[str] = set()

    def i16(where: str, key: str, v: object) -> int:
        if isinstance(v, bool) or not isinstance(v, int) or not -32768 <= v <= 32767:
            raise ConfigError(f"{where}: {key} must be an integer within the int16 range")
        return v

    for i, d in enumerate(raw):
        where = f"[[telecommands.command]] #{i + 1}"
        unknown = set(d) - {
            "name", "type", "label", "group", "subsystem", "param_int", "param_min", "param_max", "confirm", "note",
        }  # fmt: skip
        if unknown:
            raise ConfigError(f"{where}: unknown key(s) {sorted(unknown)}")
        name = d.get("name")
        if not isinstance(name, str) or not name or " " in name:
            raise ConfigError(f"{where}: name must be a non-empty string without spaces")
        where = f"{where} {name}"
        if name in seen:
            raise ConfigError(f"{where}: listed twice")
        seen.add(name)
        typ = d.get("type")
        if typ not in TelecommandType.__members__:
            raise ConfigError(f"{where}: unknown telecommand type {typ!r}")
        sub = d.get("subsystem", 0)
        if isinstance(sub, bool) or sub not in TELECOMMAND_SUBSYSTEMS:
            raise ConfigError(f"{where}: subsystem must be 0..6")
        param = i16(where, "param_int", d.get("param_int", 0))
        lo, hi = d.get("param_min"), d.get("param_max")
        if (lo is None) != (hi is None):
            raise ConfigError(f"{where}: param_min and param_max go together")
        if lo is not None:
            lo, hi = i16(where, "param_min", lo), i16(where, "param_max", hi)
            if not lo <= param <= hi:
                raise ConfigError(f"{where}: param_int (the default) must lie within param_min..param_max")
        confirm = d.get("confirm", "hold")
        if confirm not in TELECOMMAND_CONFIRM_LEVELS:
            raise ConfigError(f"{where}: confirm must be one of {TELECOMMAND_CONFIRM_LEVELS}")
        label, group, note = d.get("label", name), d.get("group", "Other"), d.get("note", "")
        if not all(isinstance(x, str) for x in (label, group, note)) or not label or not group:
            raise ConfigError(f"{where}: label and group must be non-empty strings, note a string")
        sec.commands.append(TelecommandEntry(name, typ, label, group, sub, param, lo, hi, confirm, note))
    return sec


def parse_config(data: dict) -> GsConfig:
    """Build and validate a GsConfig from parsed TOML."""
    data = dict(data)
    cfg = GsConfig(
        core=_section(CoreSection, data.pop("core", {}), "core"),
        server=_section(ServerSection, data.pop("server", {}), "server"),
        recorder=_section(RecorderSection, data.pop("recorder", {}), "recorder"),
        sources=[_source(s) for s in data.pop("source", [])],
        radio=_section(RadioSection, data.pop("radio", {}), "radio"),
        gui=_section(GuiSection, data.pop("gui", {}), "gui"),
        requests=_requests(data.pop("requests", {})),
        telecommands=_telecommands(data.pop("telecommands", {})),
    )
    if data:
        raise ConfigError(f"unknown top-level section(s) {sorted(data)}")
    validate(cfg)
    return cfg


def validate(cfg: GsConfig) -> None:
    ids = [s.id for s in cfg.sources]
    if len(set(ids)) != len(ids):
        raise ConfigError("source ids must be unique")
    for s in cfg.sources:
        if s.id == "gs" or not s.id or " " in s.id:
            raise ConfigError(f"invalid source id {s.id!r} (must be non-empty, no spaces, not 'gs')")
        if s.kind not in SOURCE_KINDS:
            raise ConfigError(f"source {s.id}: kind must be one of {SOURCE_KINDS}")
        if s.rc_role not in RC_ROLES:
            raise ConfigError(f"source {s.id}: rc_role must be one of {RC_ROLES}")
    unknown = [i for i in cfg.core.source_priority if i not in ids]
    if unknown:
        raise ConfigError(f"core.source_priority names unknown source(s) {unknown}")
    unknown = [i for i in cfg.requests.source_preference or [] if i not in ids]
    if unknown:
        raise ConfigError(f"requests.source_preference names unknown source(s) {unknown}")
    unknown = [i for i in cfg.telecommands.source_preference or [] if i not in ids]
    if unknown:
        raise ConfigError(f"telecommands.source_preference names unknown source(s) {unknown}")
    if cfg.recorder.raw_frames != "unknown":
        raise ConfigError('recorder.raw_frames: only "unknown" is supported')
    if not 0 < cfg.server.port < 65536:
        raise ConfigError("server.port out of range")
    if cfg.core.stale_min_s <= 0:
        raise ConfigError("core.stale_min_s must be > 0")


def load_config(path: str | Path) -> GsConfig:
    path = Path(path).expanduser()
    try:
        with open(path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError:
        raise ConfigError(f"config file not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: {e}") from None
    return parse_config(data)
