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
REQUEST_TARGETS = (1, 2, 3)  # 1 vehicle, 2 TX module, 3 RX module (ExVectrNetwork node ids)


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


@dataclass
class RequestsSection:
    """`[requests]`: the only REQUESTs the ground station will ever send. Empty = none."""

    # Source ids tried in this order (a source must be connected and able to reach the target). Not set:
    # every vehicle_usb source, then every link_module source, then fake sources, each in config order.
    source_preference: list[str] | None = None
    actions: list[RequestEntry] = field(default_factory=list)


@dataclass
class GsConfig:
    core: CoreSection = field(default_factory=CoreSection)
    server: ServerSection = field(default_factory=ServerSection)
    recorder: RecorderSection = field(default_factory=RecorderSection)
    sources: list[SourceConfig] = field(default_factory=list)
    radio: RadioSection = field(default_factory=RadioSection)
    gui: GuiSection = field(default_factory=GuiSection)
    requests: RequestsSection = field(default_factory=RequestsSection)


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
        unknown = set(d) - {"action", "label", "group", "target", "confirm"}
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
        sec.actions.append(RequestEntry(name, target, label, group, confirm))
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
