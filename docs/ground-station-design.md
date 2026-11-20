# Ground Station: Architecture and Design

Status: **draft for review** (2026-11-18). Design only. Nothing here is implemented yet.
Scope: the `ground-station/` project (its own git repository, to be created per "Adding a project" in the workspace `CLAUDE.md`).
Binding inputs: [safety.md](safety.md), [system-architecture.md](system-architecture.md), the workspace `CLAUDE.md`, and the user decisions listed in the task brief (recording, replay, 3D view, virtual radio, panels, data rates).

When the repository exists, this document becomes `ground-station/docs/architecture.md` (split as described in §8.3), and this file is replaced by a pointer.

---

## 1. Goals, non-goals, safety

### 1.1 Goals

1. **Record** every packet received from the vehicle, the radio link and the ground station's own sent RC state, into human-readable, append-only, crash-tolerant text logs.
2. **Visualise** live data in a browser GUI: a 3D ship view (attitude, position, trail, setpoint, flaps) and independent data panels that show the age of each value.
3. **Replay** recorded logs (flight and simulation) with play, pause, seek and speed control. Replay is indistinguishable from live data to the panels.
4. **Virtual radio:** send a complete RC channel state continuously (bench/simulation), or mirror the Radiomaster Pocket's live state (flight tests).
5. **CLI for humans and AI agents:** read live data, wait for vehicle-reported conditions, control recording and replay, send RC state for bench testing.
6. **Survive change:** the wire protocol (framing, packet layouts, ports) will be redesigned in roadmap step 3. Swapping it must touch one plug-in module, not the core, the logs or the GUI.
7. **Run anywhere on Linux:** headless core on x86 or ARM (e.g. a Steam Frame used as a mobile PC), offline in the field. GUI on any device on the local network.

### 1.2 Non-goals

- **No control, mission, estimation or safety logic.** No closed-loop simulation, no autopilot, no "arm macro", no command sequencing (safety.md §1).
- **No legacy `Telecommand` events, except the TEMPORARY adapter of decision 10.1-10** (explicit user approval 2026-11-20, [telecommands.md](telecommands.md)). Today's port-1000 telecommands (Arm, MissionBegin, SimulationMode toggle, ...) are one-shot events and violate safety.md §2. The ground station will not send them. Commanding happens only through the virtual radio's RC channel state, plus whitelisted `REQUEST`s (calibration, save, link upload; decision 10.1-9, [requests.md](requests.md)).
- **No wire-format decisions.** Today's structs are supported through a replaceable plug-in only (§3.6).
- **No sticks on the virtual radio** in v1 (fixed configured values on unmapped channels).
- **No WebXR, plots of arbitrary fields, or map tiles** in v1. The design leaves room for them (panel registry, history API).
- **No WiFi to the TX module** (system-architecture.md: USB only).

### 1.3 Deviations from system-architecture.md (need the main session to update that file)

| system-architecture.md says | This design | Why |
|---|---|---|
| Core records "all raw frames with host timestamps"; replay goes through the same decoder. | Logs hold **decoded packets as text** (user decision). Undecodable frames are kept as `RawFrame` lines (hex), so nothing is lost. An option records the raw bytes of every frame as well (decision 10.1-2). | Text logs are in the *semantic* model, so they stay readable after the wire format changes. Replay still goes through one decode path: text codec → the same `Packet` objects → the same bus and state store as live data. |
| GS sends RC state over its USB port "whatever is on the other end"; the TX module ignores it. | **Master/slave (decision 10.1-8, 2026-11-19, supersedes the earlier "control source only" rule).** GS RC state is sent **only in master**, which is an explicit user choice, to **every connected source with `rc_role = "control"`** (several allowed, shipped config: the vehicle USB and the TX module). `rc_role = "mirror"` (or `none`) remains an option for a receive-only source. While the Pocket's `RC_STATE` arrives and nobody is master, the GS is a slave: it shows the Pocket and sends nothing. | The rejection of GS RC state is the receiver's job: the TX module drops `RC_STATE` arriving from USB towards the air, the vehicle applies its own authority rules. The GS adds **no refusal of its own** any more (the old "never send to the TX module" layer is gone, by user decision), so a TX-module bug that failed to drop it would no longer be caught on the GS side. The GS still never switches to master by itself. |

### 1.4 How the design follows safety.md

| Rule | How the ground station satisfies it |
|---|---|
| 1.1 Simulation runs on the vehicle | The GS contains no simulator. The `FakeSource` (§6.5) is a test fixture that *produces* telemetry. It is a source, not a sink, and cannot be connected to a vehicle. Simulation logs come from the vehicle's own simulator via USB. |
| 1.2 One command abstraction | The only uplinks are the RC channel state, the same thing the Pocket produces, and the whitelisted `REQUEST`s of 2.5 (decision 10.1-9; no free action number, no raw send). There is no simulation-only command and no "enter simulation" button, unless the vehicle's compiled-in channel mapping assigns it to a switch, in which case it is just another configured control. |
| 1.3 Commands are requests | The GS never addresses actuators. Outbound packets are restricted to types declared `direction = UP` in the registry (`RcState` and the whitelisted `Request`, decision 10.1-9), plus the ground station's own 1 Hz heartbeat, a fixed presence message built by the wire profile, not part of the registry, carrying no control data (user decision 2026-11-18: sent on every serial source; routers never forward it onto the air link; the GS is never a failsafe condition). The API has no raw-send operation. |
| 1.4 Display vehicle-reported state | Panels render only received packets. Nothing is inferred from sent commands. No optimistic UI: the radio widget displays the state the **core actually sends** (reported back by the core), labelled "sent", and the vehicle-reported applied state ("applied", active control source) when telemetry provides it. Derived display values (Euler angles, distance to setpoint) are computed for display only. |
| 1.5 Vehicle never depends on a client | The GS holds no flight state. RC state is never persisted: after a core (re)start every control is at its configured default (safe) position, and nothing is sent until a client explicitly takes control. GUI or core crash leads to silence on the link, which is the vehicle's failsafe trigger. |
| 1.6 Connections don't change behaviour | The GS never requests telemetry sets or rates. It consumes what each link delivers. Recording, GUI clients and replay have no effect on what is sent to the vehicle. |
| 1.7 Short sim/flight difference list | No GS behaviour depends on whether the vehicle is in simulation. The `sim` flag is displayed and recorded only. |
| 1.8 Same data formats | One decoder path for live simulation, live flight and replay (§2.2). |
| 2.1 Continuous state | The core's RC sender emits the complete channel state on a fixed timer (`send_rate_hz`, default 25 Hz), independent of clicks and GUI events. Clicks only change the core's state. |
| 2.2 / 2.3 Safe direction immediate, unsafe needs a transition | Enforced by the vehicle. The GS must not work around it: it never synthesises transitions (no automatic toggling), starts from default positions, and never resumes a previous state after a reconnect or restart. Control defaults in the radio config must be the safe positions (validated: the config declares them). |
| 2.4 Silence is the failsafe | Sending requires a **control lease**. If the controlling GUI disconnects (heartbeat lost), a CLI session ends, or a CLI keepalive lease is not renewed within its idle timeout (max 9 s), the core stops sending entirely (decisions 10.1-1 and 10.1-7). Momentary controls auto-release if not refreshed. A control configured with `lock` (SD) cannot leave its default until deliberately unlocked (decision 10.1-6). |
| 2.5 One-shot actions | **Whitelisted `REQUEST`s (user decision 2026-11-19, 10.1-9, `docs/requests.md`).** The ground station sends a configured whitelist of one-shot actions (calibration start/stop/apply, save, reset, and `LINK_UPLOAD_MODE` for the link modules) with an idempotent non-zero `req_id`, retried every 1 s (at most 5 attempts) until a matching `REQUEST_ACK` arrives. Only on a deliberate confirmation (hold 1 s in the GUI plus a modal with a 2 s disabled button for `danger` actions; `--confirm ACTION` and `--i-understand-the-link-stops` in the CLI), never automatically, never in replay, never from a source that is not connected, control hosts only, at most one new request per 500 ms per client host. The GS does not interpret an action and shows the ACK status as reported. Whether a vehicle accepts it (disarmed, on the ground) is the vehicle's decision; the link modules always obey `LINK_UPLOAD_MODE` (safety.md 2.5 exception), which is why it is `danger`. |
| 2.6 Vehicle reports what it applied | Panels show applied arm state, mode, active control source and last one-shot ID as soon as telemetry carries them. Until then the radio panel shows "applied state: not reported by vehicle". |

**Review rule for the GS repo** (goes into its `CLAUDE.md`; since 2026-11-20 it also covers the TEMPORARY legacy telecommand sender, decision 10.1-10): a change that makes the GS compute a command, gate an action on vehicle state, send anything other than `UP` types (through the RC sender or the whitelisted request sender), widen the request whitelist handling, send a request automatically, or persist RC state, is a safety change and needs user approval. The only things the GS sends are (a) RC state under the lease rules and (b) REQUESTs from the configured whitelist (decision 10.1-9).

---

## 2. Architecture

### 2.1 Module diagram

```
                     ┌──────────────────────────── ground-station core (Python, asyncio, headless) ────────────────────────────┐
                     │                                                                                                          │
 Teensy USB ─────────┤ SerialSource "usb"  ─┐                                                                                   │
 (bench/sim)         │   reader thread      │  bytes    ┌─────────────┐  Frame   ┌──────────────┐ Packet                         │
                     │                      ├─────────► │ Framer      ├────────► │ WireProfile  ├─────────┐                      │
 TX module USB ──────┤ SerialSource "link" ─┘           │ (per source)│          │ decode/encode│         │                      │
 (flight test)       │                                  └─────────────┘          └──────────────┘         ▼                      │
                     │ FakeSource "fake" ─────────────────────────── Packet ────────────────────────► ┌──────────┐               │
                     │                                                                                │ LiveBus  │──► Recorder ──► recordings/*.exlog
                     │ host-generated: LinkStats, GsEvent, Note, sent RcState ──────────────────────► └────┬─────┘   (writer thread)
                     │                                                                                     │                     │
                     │                                     ┌────────────────┐                              ├──► StateStore(live) + History(live)
                     │  RcController ── RcState (UP) ────► │ RC sender timer│──► WireProfile.encode ──► every control source      │
                     │   (leases, radio config)            └────────────────┘                              │                     │
                     │                                                                                     │                     │
                     │  Replayer ── reads *.exlog via TextCodec ──► ReplayBus ──► StateStore(replay) + History(replay)           │
                     │                                                                                                          │
                     │  ApiServer (aiohttp): /ws (JSON protocol §4), static GUI files, configs ◄── Hub (per-client subscriptions)│
                     └─────────────────────────────────────────────▲──────────────────────────▲─────────────────────────────────┘
                                                                   │ WebSocket                │ WebSocket
                                                  Browser GUI (Three.js, ES modules)     exgs CLI (humans, Claude)
                                                  any device on the LAN                  same host (or LAN)
```

### 2.2 Data flow

- **Live:** source bytes → `Framer.feed()` → `Frame` → `WireProfile.decode()` → `Packet` (with `t` = host receive time, `src` = source id) → `LiveBus.publish()` → fan-out to Recorder, live StateStore, live History and the API Hub.
- **Replay:** `.exlog` line → `TextCodec.decode()` → the same `Packet` class → `ReplayBus` → replay StateStore, History and Hub. The GUI receives the same message types with `view = "replay"`.
- **Uplink:** GUI/CLI `rc.set` → `RcController` state → timer → `RcState` packet (`src = "gs"`) → `WireProfile.encode()` → `Framer.encode()` → every connected `rc_role = "control"` source (master only). Each sent state is also published on the LiveBus (recorded on change plus at 1 Hz, §3.5).
- **Host-generated packets** (link statistics, GS events, operator notes) are ordinary packet types on the LiveBus, so they are recorded and displayed like vehicle data.

Live and replay run side by side. Recording always follows the **live** bus, so a recording continues while someone browses a replay.

### 2.3 Threading and async model

- **One asyncio event loop** (main thread) runs sources' control tasks, framing and decoding, bus dispatch, state stores, the RC sender timer, the replay clock and the aiohttp server. Decoding with `struct` is cheap. Budget: < 50 µs per packet for dispatch, for 2000 packets/s with large margin.
- **One reader thread per serial port**: blocking `read()` with a 50 ms timeout, then `loop.call_soon_threadsafe(source.on_bytes, data, t_ns)`. Writes go through a per-port writer thread with a small queue (bounded, newest wins for RC state).
- **One recorder writer thread**: consumes encoded lines from a queue, so disk latency and `fsync` never block the loop.
- **Replay index building** runs in a worker thread (`loop.run_in_executor`).
- **Bus dispatch is synchronous** and handlers must not block. Handlers that do I/O (recorder, WebSocket clients) put work into their own queue.
- **Slow WebSocket clients never block the core:** per-client outgoing queue. `latest` subscriptions coalesce (only the newest packet per type and source is kept), `all` subscriptions use a bounded queue that drops the oldest and reports a `dropped` count.
- **Time:** at start the core takes an epoch anchor `E = time.time_ns()` and a monotonic anchor `M = time.monotonic_ns()`. Host timestamps are `E + (monotonic_ns() − M)`: absolute, monotonic, immune to NTP steps during a session.

### 2.4 Module interfaces

Signatures are Python type-hinted declarations (no bodies). Module paths refer to the repo layout in §8.1. All times are `int` nanoseconds unless stated otherwise.

#### Packets and registry (`exvectr_gs.packets`)

```python
class Direction(Enum): DOWN = "down"; UP = "up"; LOCAL = "local"   # LOCAL = host-generated, never on the wire

@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: Literal["float", "int", "bool", "enum", "str", "time_ns", "float_array", "int_array"]
    length: int | None = None           # for arrays
    unit: str = ""                      # SI unit string, e.g. "m", "m/s", "rad", "rad/s", "N", "us", "dBm", "%"
    enum: type[IntEnum] | None = None
    optional: bool = True               # None allowed (field not supplied by this link/profile)
    desc: str = ""
    labels: tuple[str, ...] = ()        # element names for arrays, e.g. ("w","x","y","z"), ("tl","tr","bl","br")

class Packet:                           # base for all packet dataclasses
    TYPE: ClassVar[str]                 # unique name, e.g. "Attitude"
    VERSION: ClassVar[int]
    t: int                              # host receive time, ns since Unix epoch (an ordinary field)
    src: str                            # source id that delivered it ("usb", "link", "fake", "gs")

@dataclass(frozen=True)
class PacketType:
    name: str
    version: int
    cls: type[Packet]
    fields: tuple[FieldSpec, ...]       # excluding t and src
    direction: Direction
    nominal_rate_hz: float | None       # documentation and staleness default
    history: bool                       # keep a time series for trails/plots
    codec: "TextCodec"

def packet(name: str, version: int, direction: Direction, nominal_rate_hz: float | None = None,
           history: bool = False, desc: str = "", codec: "TextCodec | None" = None) -> Callable[[type], type]: ...
def f(unit: str = "", desc: str = "", labels: tuple[str, ...] = (), optional: bool = True) -> Any: ...  # dataclass field metadata

class PacketRegistry:
    def register(self, ptype: PacketType) -> None: ...            # raises on duplicate name
    def get(self, name: str) -> PacketType: ...                   # KeyError if unknown
    def all(self) -> list[PacketType]: ...
    def schema(self) -> dict: ...                                 # JSON-serialisable, sent to clients (§4)
    def markdown(self) -> str: ...                                # generates docs/packet-types.md

REGISTRY: PacketRegistry   # populated by importing exvectr_gs.packets.defs
```

#### Text codec (`exvectr_gs.packets.textcodec`)

```python
class TextCodec(Protocol):
    def encode(self, pkt: Packet) -> str: ...          # one line, no newline
    def decode(self, line: str, ptype: PacketType) -> Packet: ...   # raises CodecError

class GenericTextCodec(TextCodec): ...   # default for every type, driven by FieldSpecs (format in §3.4)

def encode_line(pkt: Packet) -> str: ...               # dispatches via REGISTRY
def decode_line(line: str) -> Packet | None: ...       # None for comment/meta lines; unknown type -> UnknownPacket
```

Contract: `decode_line(encode_line(p)) == p` for every registered type and every representable value, including NaN, ±inf, `None` fields and unknown enum values (property-tested, §8.2).

#### Wire profile and framer (`exvectr_gs.wire`)

```python
@dataclass(frozen=True)
class Frame:
    channel: int            # transport port or packet id, whatever the profile uses to select a layout
    payload: bytes
    t: int                  # host time of the last byte
    meta: Mapping[str, Any] # profile-specific (source/destination node, sequence, ...)

@dataclass(frozen=True)
class ConsoleText:          # non-frame bytes on the stream (e.g. Teensy Serial.print), split into lines
    text: str
    t: int

class Framer(Protocol):
    def feed(self, data: bytes, t: int) -> Iterable[Frame | ConsoleText]: ...
    def encode(self, frame: Frame) -> bytes: ...
    def reset(self) -> None: ...                        # called on (re)connect
    stats: "FramerStats"                                # bytes, frames, crc_errors, resyncs

class WireProfile(Protocol):
    name: str                                           # e.g. "provisional-1", later "exvectrnet-2"
    def make_framer(self) -> Framer: ...
    def decode(self, frame: Frame, src: str) -> Packet: ...        # unknown/invalid -> RawFrame packet, never raises
    def encode(self, pkt: Packet) -> Frame | None: ...             # None if this profile cannot send the type
    def supported_types(self) -> set[str]: ...

def load_profile(name: str) -> WireProfile: ...         # entry point lookup: exvectr_gs.wire.<name>
```

#### Sources (`exvectr_gs.sources`)

```python
class SourceKind(Enum): VEHICLE_USB = "vehicle_usb"; LINK_MODULE = "link_module"; FAKE = "fake"

@dataclass(frozen=True)
class SourceCaps:
    rc_role: Literal["control", "mirror", "none"]   # control: RC-capable, master sends GS RC state here (several allowed). mirror/none: never sent to.
    can_send: bool

@dataclass
class SourceStatus:
    id: str; kind: SourceKind; enabled: bool
    state: Literal["disabled", "searching", "connected", "error"]
    device: str | None; since: int; reconnects: int; last_error: str | None

PacketSink = Callable[[Packet], None]

class Source(ABC):
    id: str
    kind: SourceKind
    caps: SourceCaps
    @abstractmethod
    async def run(self, sink: PacketSink) -> None: ...    # runs until cancelled, handles reconnects itself, never raises
    @abstractmethod
    async def send(self, pkt: Packet) -> bool: ...        # False if not connected or not encodable
    @abstractmethod
    def status(self) -> SourceStatus: ...

def build_source(cfg: SourceConfig, profile: WireProfile) -> Source: ...
```

Every source also emits `LinkStats` packets for itself at 1 Hz (§6.3).

#### Bus, state store, history (`exvectr_gs.core`)

```python
class Bus:
    def subscribe(self, handler: PacketSink, types: set[str] | None = None) -> Callable[[], None]: ...  # returns unsubscribe
    def publish(self, pkt: Packet) -> None: ...

@dataclass
class Entry:
    packet: Packet
    count: int
    interval_s: float | None     # EWMA of inter-arrival time for this (type, src)
    stale_after_s: float         # max(3 * interval_s, cfg.stale_min_s), or 3/nominal_rate if no interval yet

class StateStore:
    def __init__(self, registry: PacketRegistry, source_priority: list[str], stale_min_s: float = 0.5): ...
    def update(self, pkt: Packet) -> None: ...
    def get(self, type_name: str, src: str | None = None, now: int | None = None) -> Entry | None: ...
        # src=None: highest-priority source whose entry is fresh at `now`, else the newest entry
    def snapshot(self) -> list[Entry]: ...                # latest per (type, src)
    def reset(self, entries: Iterable[Packet] = ()) -> None: ...   # used by replay seek

class History:
    def __init__(self, registry: PacketRegistry, window_s: float = 600, max_hz: float = 20): ...
    def update(self, pkt: Packet) -> None: ...            # only types with history=True, downsampled to max_hz
    def query(self, type_name: str, src: str | None, fields: list[str], t0: int, t1: int,
              max_points: int) -> dict[str, list]: ...     # {"t": [...], "<field>": [...]}
```

#### Recorder and log files (`exvectr_gs.recording`)

```python
@dataclass
class RecordingInfo: path: Path; name: str; started: int; stopped: int | None; packets: int; bytes: int

class Recorder:
    def __init__(self, directory: Path, registry: PacketRegistry, bus: Bus, meta: Mapping[str, str], *,
                 sources=(), clock=time.time_ns, flush_interval_ms=100, fsync_interval_ms=1000,
                 on_error=None, opener=None, max_queue=500_000): ...   # keyword-only extras added in T2
    def start(self, label: str | None = None) -> RecordingInfo: ...   # new file, never overwrites; raises if already recording
    def stop(self) -> RecordingInfo | None: ...                        # flush, fsync, trailer
    def note(self, text: str) -> None: ...                             # publishes a Note packet (recorded if active)
    def source_connected(self, info: Mapping[str, str]) -> None: ...  # writes a #source line while recording
    def status(self) -> "RecorderStatus": ...                          # active, info, last_error, free_bytes, dropped

class LogReader:                                     # shared by Replayer, LogLibrary and `exgs log` tools
    def __init__(self, path: Path): ...
    def header(self) -> dict[str, str]: ...
    def iter_packets(self, start_offset: int = 0) -> Iterator[tuple[int, Packet]]: ...   # (byte offset, packet); skips a truncated last line
    def sources(self) -> list[dict[str, str]]: ...   # #source lines of the header block
    def build_index(self, keyframe_s: float = 1.0, history_hz: float = 20.0) -> "LogIndex": ...

@dataclass
class LogInfo: name: str; path: Path; started: int; duration_s: float | None; size: int
               complete: bool; sources: list[str]; sim: bool | None; label: str | None

class LogLibrary:
    def __init__(self, directory: Path): ...
    def list(self) -> list[LogInfo]: ...             # newest first; reads header + trailer (or tail for incomplete files)
```

#### Replayer (`exvectr_gs.replay`)

```python
@dataclass
class ReplayStatus:
    state: Literal["closed", "loading", "paused", "playing", "ended"]
    name: str | None; t: int | None; t_start: int | None; t_end: int | None; speed: float

class Replayer:
    def __init__(self, library: LogLibrary, bus: Bus, store: StateStore, history: History): ...
    async def open(self, name: str) -> LogInfo: ...  # builds index (worker thread), seeks to t_start, paused
    def close(self) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def seek(self, t: int) -> None: ...              # clamps to [t_start, t_end]; keeps play/pause state
    def set_speed(self, speed: float) -> None: ...   # allowed: 0.25, 0.5, 1, 2, 4, 8
    def status(self) -> ReplayStatus: ...
```

#### RC controller (`exvectr_gs.rc`)

```python
@dataclass
class Lease: id: str; holder: str; kind: Literal["connection", "keepalive"]; expires: int | None; idle_s: float | None = None
# keepalive: expires = last renewal + idle_s (default 5, max 9), renewed by every lease operation; connection: expires = None

@dataclass
class RcStatus:
    mode: Literal["master", "slave", "idle"]
    sending: bool; target: str | None; lease: Lease | None   # target: ids of the sources sent to, comma separated
    controls: dict[str, float | int]   # sent state (master) or the Pocket's state (slave), by control id
    targets: list[str]                 # ids of the sources sent to (master)
    pocket_active: bool                # a fresh handset RcState is arriving (master: warning banner)
    channels_us: list[int]             # full channel vector of that state
    origin: Literal["gs", "handset"] | None; t: int | None
    locks: dict[str, dict]             # lockable control id -> {locked, remaining_s, timeout_s}

class RcController:
    def __init__(self, cfg: RadioConfig, sources: Sequence[Source], bus: Bus): ...
    def acquire(self, holder: str, kind: str, idle_s: float | None = None, remote_addr: str = "") -> Lease: ...
        # raises ControlDenied (another holder, no RC-capable source, remote not allowed)
    def release(self, lease_id: str) -> None: ...
    def heartbeat(self, lease_id: str) -> None: ...  # connection leases: at least every 1 s; keepalive leases: a renewal
    def set(self, lease_id: str, values: Mapping[str, float | int], hold_s: float | None = None) -> RcStatus: ...
        # by control id; validated; raises ControlLocked for a non-default value on a locked control (nothing applied)
    def unlock(self, lease_id: str, control: str) -> RcStatus: ...  # control has `lock`; relocks after lock_timeout_s
    def lock(self, lease_id: str, control: str) -> RcStatus: ...    # relock at once, control back to its default
    def status(self) -> RcStatus: ...
    async def run(self) -> None: ...                 # sender timer + lease/momentary expiry + mirror detection
```

#### API server and CLI

- `ApiServer(app_ctx, bind: str, port: int)`: aiohttp application with `GET /` (GUI), `GET /static/...`, `GET /config/{radio|layout|model}.json`, `GET /models/...` (glTF files), `GET /ws` (protocol §4).
- `Hub`: one per server. It tracks clients and their subscriptions per view, and pushes `packets`, `status`, `rc` and `event` messages.
- CLI `exgs` (§4.4) is a WebSocket client of the core, except `exgs core` (runs the core) and the offline `exgs log ...` tools.

### 2.5 Configuration

(The `[requests]` whitelist and `source_preference` are described in [requests.md](requests.md); `config/gs.example.toml` ships the full list.)

One TOML file (`gs.toml`, path via `--config`, default `~/.config/exvectr-gs/gs.toml`). Example:

```toml
[core]
wire_profile = "provisional-1"
source_priority = ["usb", "link", "fake"]     # preferred source per value when several are fresh
stale_min_s = 0.5

[server]
bind = "0.0.0.0"
port = 8765
control_hosts = ["127.0.0.1", "::1"]          # who may take RC control (others are view-only)

[recorder]
directory = "~/exvectr-recordings"
raw_frames = "unknown"                         # only undecodable frames (decision 10.1-2); "all" is not offered
flush_interval_ms = 100
fsync_interval_ms = 1000
# Recording starts and stops only by explicit user action (decision 10.1-3): no auto-start, no resume after a crash.

[[source]]
id = "usb"
kind = "vehicle_usb"
match = { vid = "16c0", pid = "0483" }         # Teensy USB serial; or device = "/dev/serial/by-id/..."
rc_role = "control"

[[source]]
id = "link"
kind = "link_module"
match = { device = "/dev/serial/by-id/usb-Silicon_Labs_CP2102_XXXX-if00-port0" }
baud = 921600
rc_role = "control"                            # master sends here too; the TX module rejects it. "mirror" = receive-only
enabled = false

[radio]
config = "config/radio.pocket.toml"

[gui]
layout = "config/layout.default.json"
model = "config/model.placeholder.json"
```

Validation at start: any number of sources may have `rc_role = "control"` (master sends to all of them, decision 10.1-8). Source ids are unique and do not equal `gs`.

---

## 3. Data model

### 3.1 Declaring packet types (one place)

All semantic packet types are declared in `src/exvectr_gs/packets/defs/` as decorated dataclasses. This is the single source of truth for field names, units, enums, rates and text format. The wire profile maps binary layouts *onto* these classes. The GUI receives the schema from the core, and `docs/packet-types.md` is generated from it (`exgs schema --markdown`).

Example declaration:

```python
class VehicleMode(IntEnum): Startup = 0; SensorInit = 1; SensorCalib = 2; Ready = 3; Running = 4; Failure = 5

@packet("Attitude", version=1, direction=Direction.DOWN, nominal_rate_hz=5, history=True,
        desc="Attitude estimate. q rotates body -> world (NWU).")
class Attitude(Packet):
    omega: list[float] = f(unit="rad/s", labels=("x", "y", "z"), desc="angular velocity, body frame")
    q: list[float] = f(labels=("w", "x", "y", "z"), desc="unit quaternion, body -> world")
    tilt_acc: float | None = f(unit="rad")
    north_acc: float | None = f(unit="rad")
```

Rules:
- Names are `CamelCase` types and `snake_case` fields. SI units throughout (workspace convention). Time fields are `time_ns` (int64 ns).
- Every field except `t` and `src` is optional (`None` = not supplied). The radio telemetry set will carry fewer fields than the USB debug set, and panels must show "not supplied" rather than 0.
- Adding a field is backward compatible (old logs decode with `None`). Removing, renaming or changing a unit increments `version`. The codec keeps decoding older versions through a per-type `migrate(version, fields) -> fields` hook.
- Enums decode unknown numeric values without failing (shown as `#7`).
- Vehicle-supplied time (e.g. `t_vehicle`, `t_mission`) is an ordinary `time_ns` field where the vehicle provides it. `t` (host receive time) is the replay timeline because every packet has it, including host-generated ones.

### 3.2 Initial packet set

Derived from today's telemetry (`flight-software/docs/project/hardware-and-drivers.md` §9, `ExVectrPackets.md`) plus host/link types. Planned types are marked. The final set follows the protocol redesign; changing it is a registry edit plus a wire-profile edit.

| Type | Dir | Fields (unit) | Source today |
|---|---|---|---|
| `Attitude` | down | `omega[3]` (rad/s), `q[4]` (w,x,y,z), `tilt_acc`, `north_acc` (rad) | `PacketAttitude`, port 10, 5 Hz |
| `Position` | down | `pos[3]` (m, NWU), `vel[3]` (m/s), `h_acc`, `v_acc` (m) | `PacketPosition`, port 11, 10 Hz. Today's accuracies have no physical unit (known issue). The profile passes them through, flagged in the field `desc`. |
| `Gps` | down | `lat`, `lon` (**rad**), `alt` (m), `vel[3]` (m/s), `num_sats`, `pos_acc`, `alt_acc` (m), `vel_acc` (m/s) | `PacketGPS`, port 12, 1 Hz. The wire holds rad × 1e7 (known issue). The legacy layout decodes it as radians. |
| `VehicleState` | down | `mode` (enum), `sensor_{gnss,imu,baro,mag,att_kf,pos_kf}` (enum), `fail_{sensor,attitude,position,pos_oob,att_oob,radio_loss}` (bool), `sim`, `armed` (bool), `tvc_thrust[3]` (N), `tvc_torque` (N·m), `ground_dist` (m), `ground_dist_cov`. Planned: `control_source` (enum), `last_oneshot_id`. | `VehicleState`, port 50 |
| `MissionState` | down | `sp_vel[3]` (m/s), `sp_pos[3]` (m), `mission_mode`, `mission_phase` (enum), `t_mission` (ns, negative in countdown), `mission_index` | `MissionState`, port 51 |
| `FlapState` | down | `angle[4]` (rad; labels tl,tr,bl,br; 90° retracted, 0° extended), `cmd[4]` (rad), `enabled` (bool) | planned |
| `TvcState` | down | `vane[4]` (rad; xp,xn,yp,yn), `motor[2]` (0..1), `enabled`, `motors_enabled` (bool) | planned, USB debug set |
| `RcState` | up/down | `origin` (enum `gs`/`handset`), `ch_us[16]` (µs, 1000–2000), `seq` | GS sender. Pocket state via TX module (planned). |
| `RadioLinkStats` | down | `rssi_dbm[2]`, `lq_pct`, `snr_db`, `up_hz`, `down_hz`, `antenna` | TX module (planned) |
| `LinkStats` | local | `connected` (bool), `rx_bytes_s`, `rx_frames_s`, `tx_frames_s`, `crc_errors`, `decode_errors`, `reconnects` | generated per source by the GS |
| `ConsoleText` | local | `text` (str) | non-frame bytes on a serial stream (Teensy prints) |
| `RawFrame` | local | `channel`, `len`, `hex` (str), `err` (str) | frames the profile could not decode |
| `GsEvent` | local | `kind` (enum: rec_start, rec_stop, source_up, source_down, lease_acquired, lease_released, lease_expired, replay_note), `detail` (str) | GS |
| `Note` | local | `text` (str) | operator note from GUI/CLI |

### 3.3 Log file

- Extension `.exlog`, UTF-8, `\n` line endings, one packet per line, append-only.
- Lines starting with `#` are metadata:
  - `#exlog v=1`: first line, format version.
  - `#meta key=value ...`: GS version, host, wire profile, config hash, start time (ISO 8601 with offset), label.
  - `#source ...`: one per configured source at start and on each connect (id, kind, device, rc_role).
  - `#type ...`: written before the first packet of each type in the file. It lists version, fields and units, so a log documents itself even if the registry changes later.
  - `#end ...`: written on clean stop (stop time, packet count). Its absence marks an incomplete (crashed) recording, which stays fully readable up to its last complete line.
- **Naming:** `<directory>/YYYY-MM-DD_HH-MM-SS[_label].exlog` in local time (e.g. `2026-11-18_14-03-12_hover-test.exlog`). The file is created with `open(path, "x")` (O_EXCL), so an existing file is never overwritten. On a collision (two starts in the same second) a suffix `-2`, `-3`, ... is added.
- **Crash behaviour:** the writer thread writes lines as they come and calls `flush()` at least every `flush_interval_ms` (100 ms) and `os.fsync()` every `fsync_interval_ms` (1 s) and on stop. A core crash loses at most the last ~100 ms (unflushed user-space buffer). An OS crash or power loss loses at most ~1 s. The reader ignores a truncated final line. After a crash the old file stays as it is (valid, readable up to its last complete line, no `#end`). The core does **not** start a recording by itself after a restart (decision 10.1-3): the user starts a new recording manually, which always goes to a new file. The recorder never appends to an old file.
- **Disk full / write error:** the recorder stops, reports the error (`Recorder.status().last_error` and the `on_error` callback, which the API turns into an `event` of level error for all clients), and the core keeps running. The partial file stays valid. The user starts a new recording manually.
- **Size estimate:** ~120 bytes/line. USB at 1500 packets/s ≈ 650 MB/h. Radio at ~60 packets/s ≈ 26 MB/h. Compression (`exgs log compress` → `.exlog.gz`, readable by `LogReader`) is a later tool.

### 3.4 Text line format

```
<t> <src> <Type> <field>=<value> <field>=<value> ...
```

- `t` (int ns since Unix epoch) and `src` are the base `Packet` fields, written positionally first so that indexing can read the time without parsing the line.
- Fields in declaration order. `None` fields are omitted.
- Values:

| Kind | Text | Example |
|---|---|---|
| float | shortest round-trip repr (`repr(float)`), `nan`, `inf`, `-inf` | `0.0123` |
| int, time_ns | decimal | `-9500000000` |
| bool | `true` / `false` | `armed=false` |
| enum | member name, or `#<n>` for unknown values | `mode=Ready`, `mode=#9` |
| str | JSON string (double-quoted, escaped) | `text="gust from west"` |
| arrays | `[v,v,...]` without spaces, elements as above | `q=[0.99981,0.0102,-0.0163,0.0021]` |

- Unknown field names on decode are preserved in `Packet.extra` (dict) and re-encoded, so a newer log survives an older reader. Unknown types decode to `UnknownPacket(type, raw_fields)`.
- A type may supply its own `TextCodec` (registered in the decorator), but must keep the leading `<t> <src> <Type>` columns.

Example log (made-up values):

```
#exlog v=1
#meta started=2026-11-18T14:03:12.481+01:00 gs=0.1.0 host=fieldpc profile=provisional-1 label=hover-test
#source id=link kind=link_module device=/dev/serial/by-id/usb-Silicon_Labs_CP2102_0001-if00-port0 rc_role=mirror
#type GsEvent v=1 kind:enum detail:str
1763471000100000000 gs GsEvent kind=rec_start detail="manual"
#type Attitude v=1 omega:float[3]:rad/s q:float[4] tilt_acc:float:rad north_acc:float:rad
1763471000123456789 link Attitude omega=[0.0123,-0.0041,0.0007] q=[0.99981,0.0102,-0.0163,0.0021] tilt_acc=0.0035 north_acc=0.021
#type Position v=1 pos:float[3]:m vel:float[3]:m/s h_acc:float:m v_acc:float:m
1763471000125001000 link Position pos=[0.12,-0.31,2.47] vel=[0.02,-0.01,0.85] h_acc=0.4 v_acc=0.2
#type VehicleState v=1 mode:enum sensor_imu:enum ... armed:bool sim:bool
1763471000140220000 link VehicleState mode=Running sensor_gnss=Ready sensor_imu=Ready sensor_baro=Ready sensor_mag=Ready sensor_att_kf=Ready sensor_pos_kf=Ready fail_sensor=false fail_radio_loss=false sim=false armed=true tvc_thrust=[0.12,-0.05,11.8] tvc_torque=0.01
#type MissionState v=1 sp_vel:float[3]:m/s sp_pos:float[3]:m mission_mode:enum t_mission:time_ns mission_index:int mission_phase:enum
1763471000150000000 link MissionState sp_vel=[0,0,0.5] sp_pos=[0,0,3] mission_mode=Running t_mission=4250000000 mission_index=0 mission_phase=Ascent
#type RcState v=1 origin:enum ch_us:int[16]:us seq:int
1763471000160000000 link RcState origin=handset ch_us=[1500,1500,1000,1500,2000,1000,1500,1000,1500,1500,1500,1500,1500,1500,1500,1500] seq=8812
#type RadioLinkStats v=1 rssi_dbm:float[2]:dBm lq_pct:float:% snr_db:float:dB up_hz:float:Hz down_hz:float:Hz
1763471000200000000 link RadioLinkStats rssi_dbm=[-71,-74] lq_pct=98 snr_db=9.5 up_hz=50 down_hz=48
#type LinkStats v=1 connected:bool rx_bytes_s:float rx_frames_s:float crc_errors:int reconnects:int
1763471000300000000 link LinkStats connected=true rx_bytes_s=8540 rx_frames_s=61 crc_errors=0 reconnects=0
#type RawFrame v=1 channel:int len:int hex:str err:str
1763471000412000000 link RawFrame channel=77 len=4 hex="0a1f00c8" err="unknown channel"
#type Note v=1 text:str
1763471001000000000 gs Note text="gusts from the west, ~3 m/s"
#end stopped=2026-11-18T14:21:40.002+01:00 packets=66410
```

### 3.5 What gets recorded

Recording runs only between an explicit Start and Stop by the user (decision 10.1-3), never automatically, also not after a crash or restart. While it runs, everything on the LiveBus: all decoded packets from every live source, `RawFrame` (only frames the wire profile could not decode, decision 10.1-2; there is no option to record the raw bytes of every frame), `ConsoleText`, `LinkStats`, `RadioLinkStats`, `RcState` from the handset (every packet) and from the GS (every change plus 1 Hz while sending, so the log shows exactly what was commanded without 25 Hz noise), `GsEvent` and `Note`. Replay packets are never recorded.

### 3.6 Isolating the wire format

- **Semantic layer** (registry, text codec, logs, state store, API, GUI) knows only packet types and fields.
- **Wire layer** (one `WireProfile` package) knows framing, channels/ports, byte layouts, fixed-point scaling and known unit quirks.
- Profiles in v1:
  - `provisional-1`: today's payload layouts (`PacketAttitude`/`Position`/`GPS`, `VehicleState`, `MissionState` on ports 10/11/12/50/51, decoded exactly as documented, including the radians/variance quirks), wrapped in a **provisional framer** (COBS-delimited: `channel u16 | len u16 | payload | crc16`). No vehicle speaks this framer yet. It exists so that the serial path can be built and tested end-to-end against the fake vehicle (§6.5). It will be replaced by the real ExVectrNetwork-over-USB framing from roadmap step 3 (decision 10.1-5).
  - `fake`: no bytes. `FakeSource` emits packets directly.
- When the protocol redesign lands: write a new profile (`exvectrnet-2`), adjust the registry for any new or changed semantic types, and select it in `gs.toml`. Recorded logs remain readable because they are semantic. Logs holding `RawFrame`s can be re-decoded with a newer profile (`exgs log redecode --profile X in.exlog out.exlog`).
- Ideally the shared protocol library (system-architecture.md, "Shared protocol definitions") later becomes the source for both the binary layouts and the registry declarations (code generation). The registry format here is chosen so that it can be generated.

### 3.7 State store and staleness

- The store keeps the latest `Entry` per `(type, src)` with a measured inter-arrival EWMA (α = 0.2).
- `stale_after_s = max(3 × interval_s, stale_min_s)`. Before an interval is known, `3 / nominal_rate_hz` is used. Because the threshold follows the measured rate, the slow radio set and the fast USB set are both judged fairly.
- Freshness states, computed against the **view clock** (live: core time; replay: replay position):
  - `fresh`: age ≤ `stale_after_s`.
  - `stale`: shown greyed with its age ("4.2 s ago"), never styled as current.
  - `missing`: never received in this session/view ("—").
  - `not supplied`: received, but this field is `None`.
- **Preferred source:** `get(type)` without `src` returns the highest-priority source (`source_priority`) whose entry is fresh, else the newest entry. Panels show a small source tag (e.g. `usb`, `link`) next to each group of values.

---

## 4. Core ↔ client API (WebSocket)

### 4.1 Transport and conventions

- `ws://<host>:8765/ws`, JSON text messages, one object per message. Protocol version `1`.
- Every message has `op`. Requests may carry `id` (client-chosen). The reply is `{"op":"result","id":…,"ok":true,"data":{…}}` or `{"op":"result","id":…,"ok":false,"error":{"code":"…","message":"…"}}`.
- **Times in JSON are float seconds since the Unix epoch** (`t`, `clock`, `expires`), and durations are float seconds. Reason: int64 ns since the epoch (~1.7e18) exceeds JavaScript's exact integer range (2^53 ≈ 9e15). A double gives sub-µs resolution at today's epoch. `time_ns` fields inside packets (e.g. `t_mission`) are converted to float seconds the same way. Logs keep integer ns.
- Packet JSON form: `{"type":"Attitude","src":"usb","t":1763471000.1234568,"f":{"omega":[…],"q":[…],"tilt_acc":0.0035}}`. Enums are sent as names, NaN/inf as `null` plus `"nf":{"field":"nan"}`. `None` fields are omitted.
- Error codes: `bad_request`, `unknown_type`, `not_found`, `busy`, `control_denied`, `no_lease`, `invalid_value`, `io_error`.

### 4.2 Client → core

| op | Payload | Effect |
|---|---|---|
| `hello` | `client: "gui"\|"cli"`, `name`, `protocol: 1` | Must be first. Answered by `welcome`, then `schema`. |
| `subscribe` | `view: "live"\|"replay"`, `types: [..]\|"*"`, `mode: "latest"\|"all"`, `max_rate_hz` (default 30) | Replaces this client's subscription for that view. Followed by a `snapshot`. |
| `unsubscribe` | `view` | |
| `get_snapshot` | `view` | Reply `data` = snapshot body. |
| `get_history` | `view`, `series: [{type, src?, fields: [..]}]`, `t0`, `t1`, `max_points` | Reply with `{"series":[{"type","src","t":[..],"<field>":[..]}]}`. Used for the 3D trail. |
| `rec.start` | `label?` | Reply: `RecordingInfo`. |
| `rec.stop` | | Reply: `RecordingInfo`. |
| `rec.note` | `text` | |
| `logs.list` | | Reply: `[LogInfo]`. |
| `replay.open` | `name` | Loads the log. Replay view starts paused at the beginning. |
| `replay.close` / `replay.play` / `replay.pause` | | |
| `replay.seek` | `t` (absolute seconds) | |
| `replay.speed` | `x` ∈ {0.25, 0.5, 1, 2, 4, 8} | |
| `rc.acquire` | `kind: "connection"\|"keepalive"`, `idle_s?` (keepalive only, default 5, max 9) | Reply: `Lease`. Denied if another holder exists, no RC-capable (`rc_role = "control"`) source is connected, or the client's address is not in `control_hosts`. (The 600 s `timed` lease was removed, decision 10.1-7.) |
| `rc.release` | | Stops sending immediately. |
| `rc.heartbeat` | | Connection leases: required at least every 1 s (GUI and CLI session send every 300 ms). Keepalive leases: a renewal (every lease operation renews). |
| `rc.set` | `values: {"SA": 1, "SB": 2, "S1": 0.42}` | Requires the lease. Values are control positions (index for switches, 0..1 for sliders). A non-default value for a locked control is rejected with `control_locked` and nothing is applied. Reply: `RcStatus`. |
| `rc.unlock` / `rc.lock` | `control` | Requires the lease. Unlock a control that has `lock` in the radio config (relocks after `lock_timeout_s`, default 60 s) or lock it again (the control returns to its default). Reply: `RcStatus` (decision 10.1-6). |
| `sources.list` | | Reply: `[SourceStatus]`. |
| `sources.enable` | `id`, `enabled` | |
| `config.get` | `name: "radio"\|"layout"\|"model"` | Also available over HTTP GET. |

### 4.3 Core → client

| op | Payload | When |
|---|---|---|
| `welcome` | `protocol`, `core_version`, `client_id`, `clock`, `can_control` (address allowed), `views: ["live","replay"]` | After `hello`. |
| `schema` | `types: [{name, version, direction, nominal_rate_hz, history, desc, fields: [{name, kind, length, unit, enum: [names], labels, desc}]}]` | After `welcome`, and if the registry is reloaded. |
| `snapshot` | `view`, `clock`, `items: [{…packet…, "iv": interval_s, "stale_after": s}]` | After `subscribe`, after a replay seek or open, after a reconnect. Clients replace their store for that view. |
| `packets` | `view`, `clock`, `items: [packet…]`, `dropped?` | Batched at `max_rate_hz`. `latest` mode: newest per (type, src) since the last batch, with `iv`/`stale_after`. `all` mode: every packet in order. |
| `status` | `clock`, `sources: [SourceStatus]`, `recorder: {active, name, started, packets, bytes, free_bytes, error}`, `replay: ReplayStatus`, `clients: n` | 2 Hz and on change. |
| `rc` | `RcStatus` (+ `holder_is_you`, `locks`, `targets`, `pocket_active`) | On change and at 5 Hz while sending or in slave. |
| `event` | `level: "info"\|"warn"\|"error"`, `text`, `t` | Source connected/lost, recording failed, lease expired, control conflict. |

**Clock for remote clients:** every `packets`/`status` message carries `clock` (the view's current time). The GUI keeps `serverOffset = clock − performance.now()/1000` per view and computes ages locally between messages. A remote device's wall clock is never used.

### 4.4 CLI (`exgs`)

Same protocol. Default output is the log line format (readable for humans and Claude); `--json` prints JSON Lines. Exit codes: 0 ok, 1 error, 2 timeout, 3 control denied.

| Command | Purpose |
|---|---|
| `exgs core [--config gs.toml]` | Run the core in the foreground (systemd user unit provided in `docs/operations.md`). |
| `exgs status` | Sources, recorder, replay, RC, clients. |
| `exgs schema [--markdown]` | Packet types and fields. |
| `exgs watch [--types A,B] [--src usb] [--view live\|replay] [--count N] [--duration S]` | Stream packets (`all` mode). |
| `exgs get Type[.field] [--src S]` | Latest value with age and freshness. |
| `exgs wait 'VehicleState.armed == true' [--timeout 5]` | Block until a vehicle-reported condition holds (`==, !=, <, >, <=, >=`, one field per expression). Intended for agent bench tests. |
| `exgs rec start [--label L]` / `rec stop` / `rec note TEXT` | Recording. |
| `exgs logs` | List recordings. |
| `exgs replay open NAME` / `play` / `pause` / `seek T` / `speed X` / `close` | Replay control (`T` in seconds from the log start). |
| `exgs rc acquire [--idle S]` / `rc keepalive` / `rc release` / `rc show` | One-shot commands hold a **keepalive lease** (idle timeout 5 s, max 9 s), renewed by every rc command and by `rc keepalive`; expiry means silence and defaults. `rc release` ends it at once. |
| `exgs rc set SA=1 SB=2 S1=0.4 [--hold S] [--unlock CTRL]` | Set controls (takes or renews the keepalive lease). `SE=1 --hold 0.5` presses the momentary control for 0.5 s. A non-default value for a locked control (SD) needs `--unlock SD` (the CLI calls `rc.unlock` first). |
| `exgs rc unlock CTRL` / `rc lock CTRL` | Unlock a locked control or lock it again. |
| `exgs rc session [--script FILE] [--keep-going]` | Long-running process holding a connection lease with a 300 ms heartbeat; commands (`set`, `unlock`, `lock`, `sleep`, `show`, `release`, `quit`) from stdin or a script file. A crash or exit ends the lease at once, heartbeat loss within 1 s. |
| `exgs log validate FILE` / `log stats FILE` / `log cat FILE [--types]` / `log redecode` | Offline tools (no core needed). |

Agent bench example:

```
exgs status --json
exgs rec start --label agent-bench
exgs rc set SA=0          # takes a keepalive lease (5 s idle) and sends the safe position first (the vehicle requires a safe->unsafe transition)
exgs rc set SA=1          # every rc command renews the lease; stay under 5 s between commands, or use `exgs rc session`
exgs wait 'VehicleState.armed == true' --timeout 5
exgs rc release           # GS stops sending; the vehicle enters its link-loss failsafe
exgs rec stop
```

---

## 5. GUI design

### 5.1 Structure

- Single page, plain ES modules, no build step. Three.js and other libraries are vendored at pinned versions (§7) and resolved by an import map in `index.html`.
- Served by the core at `http://<host>:8765/`. It reconnects automatically (exponential backoff 0.5–5 s) and rebuilds from `snapshot` after a reconnect.
- **Client store** (`store.js`): per view, the latest packet per (type, src), `iv`, `stale_after`, and the view clock. Panels read from the store and are notified per type.
- **View switch:** the header toggle `LIVE | REPLAY`. All data panels and the 3D view follow the selected view. The radio panel's behaviour in replay is described in §5.6.

### 5.2 Layout

```
┌────────────────────────────────────────────────────────────────────────────────────────────────────┐
│ ExVectr GS   [LIVE|replay]   usb ● 1412 pkt/s   link ○ searching   REC ● 00:12:31 hover-test   ⚙  │
├──────────────────────────┬─────────────────────────────────────────────┬───────────────────────────┤
│ Modes      │ Mission     │                                             │  Virtual radio            │
│ Running    │ Ascent      │                                             │  ┌─────────────────────┐  │
│ ARMED  sim │ T+4.25 s    │               3D view                       │  │ CONTROLLING via usb │  │
├──────────────────────────┤      (ship, trail, setpoint, grid,          │  │  SA  SB  SC  SD     │  │
│ Failures (all clear)     │       N/W/U axes, camera modes)             │  │  SE  [S1 ─────o──]  │  │
├──────────────────────────┤                                             │  └─────────────────────┘  │
│ Position & orientation   │                                             ├───────────────────────────┤
│ Guidance (target pose)   │                                             │  Radio link               │
├──────────────────────────┤                                             │  RSSI −71/−74  LQ 98 %    │
│ Actuators (TVC, flaps,   ├─────────────────────────────────────────────┼───────────────────────────┤
│  motors)                 │ Recording / replay transport bar            │  Console / Inspector      │
│ Sensors / estimator      │ ▶ ❚❚  ──────●───────────  03:12 / 18:27  1×│                           │
└──────────────────────────┴─────────────────────────────────────────────┴───────────────────────────┘
```

- The layout comes from `layout.json`: a CSS grid template plus a list of `{panel, area, collapsed}`. The user can collapse panels and switch between presets (`default`, `bench`, `flight`, `phone` as a single column). The chosen preset is remembered per browser in `localStorage`, with try/catch so the GUI still works without storage. Drag-to-rearrange is a later option that only edits the same layout structure.
- Narrow screens (phone): single column, the 3D view at the top, panels below.

### 5.3 Panel registry

```js
// web/app/panels/registry.js
registerPanel({
  id: "mission",                         // unique, used by layout.json
  title: "Mission state",
  consumes: ["MissionState"],            // packet types; the shell derives WebSocket subscriptions from all mounted panels
  mode: "latest",                        // "latest" (default) or "all" (needs every packet, e.g. console, plots)
  create(el, ctx) {                      // el: container element; ctx: {store, view, schema, api, clock, fmt}
    return {
      onPacket(pkt) {},                  // called per received packet of a consumed type (current view only)
      onTick(now) {},                    // ~10 Hz, for age/staleness rendering
      onViewChange(view) {},             // live <-> replay: rebuild from ctx.store
      destroy() {},
    };
  },
});
```

- Adding a panel = one file in `web/app/panels/` that calls `registerPanel`, plus an entry in `layout.json`. No core change unless a new packet type is needed.
- A shared `ctx.fmt` provides value formatting with units, freshness styling (`fresh`/`stale`/`missing`/`not supplied`), and an age badge. Panels must use it, so staleness looks the same everywhere.

### 5.4 Panels and the data they consume

| Panel | Consumes | Shows |
|---|---|---|
| Radio link | `LinkStats`, `RadioLinkStats`, `status.sources` | Per source: connected state, device, reconnects, rx rate, CRC/decode errors. Radio: RSSI per antenna, LQ, SNR, up/down rates. |
| Position & orientation | `Position`, `Attitude`, `Gps` | Position and velocity (NWU), accuracies, quaternion, derived roll/pitch/yaw (display only, ZYX convention, stated in the panel), tilt from vertical, GPS lat/lon (deg for display), satellites. |
| Modes | `VehicleState` | Vehicle mode, armed, simulation flag, active control source (when reported). Large and colour-coded, with an age badge. |
| Mission state | `MissionState` | Mission mode, phase, index, mission time (T−/T+). |
| Guidance | `MissionState`, `Position` | Target position and velocity, vector and distance from the current position to the target (display only). |
| Actuators | `VehicleState`, `FlapState`, `TvcState` | TVC thrust vector and torque, the four flap angles (commanded and actual), vane angles, motor commands, enable flags. |
| Sensors / estimator | `VehicleState`, `Attitude`, `Position`, `Gps` | Per-sensor state (Init/Calib/Ready/Failure), KF states, accuracies, ground distance. |
| Failures | `VehicleState` | Each failure flag as reported now, plus "last seen set at t" in this view, from received data only. |
| Recording / replay | `status`, `logs.list` | Start/Stop with optional label, current file, duration, size, free disk, note entry. Log list with open button. Transport bar (§5.7). |
| Virtual radio | `RcState`, `rc` | §5.6. |
| 3D view | `Attitude`, `Position`, `MissionState`, `FlapState` | §5.5. |
| Console | `ConsoleText`, `event` (mode `all`) | Vehicle text output and GS events, filterable. |
| Inspector | `"*"` | Generic: any packet type, all fields from the schema, with freshness. New data is visible here before anyone writes a panel for it. |

### 5.5 3D view

**Frames (state explicitly, test explicitly):**
- Vehicle world frame: NWU, right-handed (X North, Y West, Z Up), origin = the vehicle's position reference.
- Three.js world: right-handed, Y up.
- Approach: all vehicle content lives under one `worldRoot` group with `worldRoot.rotation.x = −π/2`. This maps NWU (x, y, z) to Three.js (x, z, −y): Three.js +X = North, +Y = Up, +Z = East. Inside `worldRoot`, positions are set directly in NWU metres and attitudes directly from the vehicle quaternion.
- Quaternion order: vehicle `q = [w, x, y, z]`, Three.js `Quaternion(x, y, z, w)`. So `ship.quaternion.set(q[1], q[2], q[3], q[0])` inside `worldRoot`. Normalise on receipt. Show "invalid attitude" if |q| deviates by more than 1 %.
- For anything placed outside `worldRoot` (rarely needed): `p3 = (p_n.x, p_n.z, −p_n.y)` and `q3 = q_R ⊗ q ⊗ q_R*` with `q_R = (w = cos(−π/4), x = sin(−π/4), 0, 0)`.
- All of this lives in one module `view3d/frames.js` with unit tests (§8.2): North → +X, West → −Z, Up → +Y; a 90° yaw about NWU Z turns body +X from North to West.
- Body frame: the ship's long axis is body +Z (up when upright), consistent with the TVC thrust vector around body +Z. Body X/Y orientation relative to the flaps comes from the model config (to be confirmed, §10 risk R6).

**Scene:**
- Ground grid on z = 0 (1 m cells, 10 m major lines), N/W/U axis triad at the origin with labels, a compass rose.
- Ship model at `Position.pos` with the `Attitude.q` orientation. Both values are taken from the preferred source. If either is stale, the model is drawn semi-transparent with a "stale" label, and the last position is not extrapolated.
- **Trail:** the position history (from `get_history` on (re)subscribe, then appended live), last 60 s by default (configurable), fading with age. On replay seek it is rebuilt from history.
- **Setpoint marker:** a ghost crosshair/sphere at `MissionState.sp_pos`, a dashed line from the ship to it, and an arrow for `sp_vel` if non-zero.
- **Cameras:** follow (orbit around the ship), fixed observer (orbit around the origin), top-down. OrbitControls.
- **TVC vane/thrust arrow:** an arrow along `VehicleState.tvc_thrust` (body frame) under the ship, scaled to the thrust.

**Model configuration** (`config/model.<name>.json`, served by the core; models in `models/`):

```json
{
  "name": "placeholder",
  "body": {
    "source": { "primitive": "starship-placeholder", "height_m": 0.9, "radius_m": 0.045 },
    "comment": "or { \"gltf\": \"models/ship.glb\", \"node\": null }",
    "model_to_body": { "rotation_deg_xyz": [90, 0, 0], "translation_m": [0, 0, -0.45], "scale": 1.0 }
  },
  "flaps": [
    { "id": "tl", "index": 0, "source": { "primitive": "flap", "size_m": [0.08, 0.12, 0.004] },
      "pivot_body_m": [0.045, 0.0, 0.30], "axis_body": [0, 1, 0], "sign": 1,
      "model_angle_deg": 90 },
    { "id": "tr", "index": 1, "...": "..." },
    { "id": "bl", "index": 2, "...": "..." },
    { "id": "br", "index": 3, "...": "..." }
  ],
  "default_flap_angle_deg": 45
}
```

- `source` is either a `primitive` (procedural placeholder geometry built in JS) or a `gltf` file with an optional `node` name, so the flaps can be separate files or nodes inside the body glTF. The placeholder therefore uses exactly the same configuration path as the real models.
- `model_to_body` converts the glTF's own frame (glTF is Y-up, metres) into the vehicle body frame. The body origin is the vehicle's reference point (the point whose position the estimator reports).
- Each flap is attached to a pivot group at `pivot_body_m` and rotates about `axis_body` (unit vector, body frame). Displayed rotation = `sign × (angle − model_angle_deg)`, where `angle` follows the vehicle convention (90° = retracted/folded, 0° = fully extended) and `model_angle_deg` is the angle at which the flap is modelled in its file.
- `index` maps the flap to element `i` of `FlapState.angle` (`tl, tr, bl, br`).
- **No flap data** (missing, or no `FlapState` yet in this view): all flaps at `default_flap_angle_deg` (45°), with the actuator panel showing "flap data: none (default 45°)". Stale flap data keeps the last angles but is marked stale.
- The GUI animates directly to the received angle (no smoothing), so the display shows what was reported.

### 5.6 Virtual radio

**Look:** an SVG drawing of a Radiomaster Pocket-like handset (outline, two gimbals as decorative circles, the screen area as a status display). Switches are drawn at positions given by the radio config. Click to operate:
- `toggle2` (SA, SD): click flips between 2 positions.
- `switch3` (SB, SC): click on the top, middle or bottom zone, or click cycles. Position indicator.
- A control with `lock = true` in the radio config (SD, decision 10.1-6) is shown as a **padlock**: hold it 1 s to unlock. It is operable only while unlocked, shows the remaining unlock time and has a Relock button; the core relocks it after `lock_timeout_s` and returns it to its default.
- `momentary` (SE): pressed while the pointer is down. The GUI sends `rc.set SE=1` on pointerdown and repeats it every 100 ms while held, then `SE=0` on pointerup, pointercancel, blur or visibility change. The core releases it automatically if not refreshed for 300 ms (lost pointerup, GUI crash).
- `slider` (S1): horizontal slider 0..1, throttled to 20 updates/s.

**Radio config** (`config/radio.pocket.toml`; core and GUI both use it):

```toml
name = "Radiomaster Pocket"
send_rate_hz = 25
channel_count = 16
channel_default_us = 1500
channel_defaults = { 3 = 1000 }      # 1-based channel -> µs for unmapped channels (e.g. throttle low)
mapping_note = "Must match the flight-software compiled-in channel table (rev ...)"

[[controls]]
id = "SA"                # stable id used by API/CLI (`lock = true` and `lock_timeout_s = 60` make a control a padlock, see below)
label = "SA · ARM"       # display only
kind = "toggle2"         # toggle2 | switch3 | momentary | slider
channel = 5              # 1-based
values_us = [1000, 2000]
default = 0              # index; MUST be the safe position
svg = { x = 40, y = 30 } # position in the radio SVG viewBox

[[controls]]
id = "SB"
label = "SB · MODE"
kind = "switch3"
channel = 6
values_us = [1000, 1500, 2000]
default = 0
svg = { x = 70, y = 30 }

[[controls]]
id = "S1"
label = "S1"
kind = "slider"
channel = 9
range_us = [1000, 2000]
default = 0.0
svg = { x = 120, y = 60 }
# ... SC (switch3), SD (toggle2), SE (momentary, values_us = [1000, 2000], default = 0)
```

The core validates the config at start (unique ids and channels, channel ≤ `channel_count`, defaults in range) and refuses to start the RC controller if it is invalid. The labels are informational. The vehicle's compiled-in table is authoritative, and a later vehicle-reported mapping revision can be compared against `mapping_note` (risk R5).

**Modes (computed by the core, `RcStatus.mode`):**

| Mode | Condition | Widget |
|---|---|---|
| `master` | A lease is held (the user switched to master) and at least one source with `rc_role = "control"` is connected. The core sends at `send_rate_hz` to every such source. Allowed while the Pocket's state also arrives. | Amber banner "MASTER · sending to usb, link · 25 Hz" and a MASTER indicator, controls enabled for the lease holder (other clients see them read-only with "MASTER (<holder>)"). Shows the **sent** state as reported by the core, and the vehicle-applied state when available. If `pocket_active`, an extra red warning banner "Pocket also active: the radio rejects ground-station controls" (display only). |
| `slave` | Handset `RcState` received within the last 1 s (any source) and not master. The GS sends nothing, automatically, with no action needed. | Blue banner "SLAVE · showing the Pocket · read-only" and a SLAVE indicator, controls disabled and set to the handset's positions (channel µs mapped back to the nearest configured position; sliders continuous; unmapped value shown as raw µs). Age badge. "Switch to master" button. |
| `idle` | Neither. Nothing is sent (the existing idle state). | Grey banner "NOT SENDING". "Switch to master" button (enabled if this client is allowed). |

- "Switch to master" acquires a connection lease (the control lease is the master switch); "Release (back to slave)" ends it, resets every control to its default and closes every lock. The GS never becomes master by itself and never rejects or filters anything: rejection of GS RC state is done by the TX module and (later) the vehicle (decision 10.1-8). "Release" or closing the tab stops sending (the WebSocket closing ends the lease at once, a missing heartbeat after 1 s).
- When control is first taken, the controls start at the core's current state, which after a core start is the configured defaults (safe positions).
- **Replay view:** the widget shows the recorded `RcState` (handset or GS) read-only, with a "REPLAY" banner. If this client holds a control lease, switching to replay view first asks "Release control and switch to replay?". A client cannot hold control while it is viewing replay, so the operator never controls the live vehicle from a screen showing old data.

### 5.7 Replay UI

- **Log list** (recording/replay panel): name, start time, duration, size, sources, `sim` (from the first `VehicleState`), complete/incomplete. Newest first. Click "Open".
- **Transport bar** (visible in replay view): play/pause, scrubber across `[t_start, t_end]` (drag to seek; while dragging, seeks are throttled to 10/s), time `mm:ss / mm:ss` from the log start, speed selector 0.25×–8×, jump ±5 s buttons, keyboard (space, ←/→). Markers on the scrubber for `GsEvent`, `Note` and vehicle mode changes (from the index).
- **Seek implementation (core):** at open, `LogReader.build_index()` scans the file once in a worker thread. It records `(t, offset)` for every line, compactly in `array('q')`, plus a keyframe every 1 s of log time holding the offset of the latest line per (type, src). It also preloads history series (types with `history=True`, downsampled to 20 Hz). `seek(t)`: take the keyframe ≤ t, decode the referenced lines into `StateStore(replay).reset()`, apply the lines from the keyframe up to t without pacing, then send `snapshot` to replay subscribers. Target: < 200 ms for a 1 h log.
- **Playback:** a replay clock advances at `speed` in 10 ms steps and publishes every line with `t ≤ clock` on the ReplayBus. At the end the state becomes `ended`.
- Replay is a core-wide session: all clients in replay view see the same position, and any client may control it.

---

## 6. Sources

### 6.1 Serial source (USB from the Teensy or the TX module)

- **Device matching:** by `/dev/serial/by-id/...` path, or by USB VID/PID (+ serial number) via `serial.tools.list_ports`. This is stable across re-enumeration (a Teensy reboot changes `/dev/ttyACM*` numbering).
- **Open:** `pyserial`, exclusive (`exclusive=True`). For ESP32 modules (CP210x/CH34x bridges), DTR and RTS are deasserted *before* opening, so that opening the port does not reset the TX module. Baud from config (ignored by Teensy USB CDC).
- **Reconnect loop:** `searching` (poll every 1 s for the device) → `connected` (reader thread running) → on `SerialException`/EIO/device gone: close, `framer.reset()`, emit `LinkStats connected=false` and `GsEvent source_down`, back to `searching`. No exponential backoff (fast reconnect after Teensy reflash or reboot). `reconnects` is counted.
- **Writes:** only `RcState` (and later `UP` types) through `send()`. If not connected, `send()` returns False (the RC sender simply skips that tick).
- **Permissions:** the user must be in the `dialout` (or `uucp`) group; `docs/operations.md` includes a udev rule for the Teensy and the TX module.

### 6.2 Two sources at the same time

- Each source has its own reader thread, framer and `src` id. All packets go onto the one LiveBus, tagged with `src`. The recorder records both streams completely (duplicates via USB and radio are both kept, so links can be compared later).
- Panels show the preferred fresh source per type (`source_priority`), with a source tag. The Inspector can show any specific source.
- In master, RC state goes to **every connected** `rc_role = "control"` source (decision 10.1-8) and never to a `mirror` or `none` source. Handset state may arrive on any source. A source that (re)appears while master is sent the safe defaults first.
- If the Pocket's state also arrives while the GS is master, the radio panel shows the warning "Pocket also active: the radio rejects ground-station controls" (§5.6). How the receiving side arbitrates is its decision (system-architecture.md open point).

### 6.3 Link-quality declaration

- **Host side (every source):** a `LinkStats` packet per source at 1 Hz: `connected`, `rx_bytes_s`, `rx_frames_s`, `tx_frames_s`, `crc_errors`, `decode_errors`, `reconnects`. Generated from `FramerStats` and the source state machine. It is an ordinary recorded packet.
- **Link side:** whatever the TX module reports (RSSI, LQ, SNR, rates) is decoded by the wire profile into `RadioLinkStats`, like any other packet.
- `SourceStatus` (in `status` messages) adds the device path, state, connection time and last error.

### 6.4 Replay as a source?

Replay is deliberately **not** a `Source` on the live bus. It feeds its own ReplayBus and view. This keeps recordings free of replayed data and lets live recording continue during replay. Panels are still indistinguishable between views because both deliver the same `Packet` objects through the same API messages.

### 6.5 Fake source and fake vehicle (development without hardware)

- **`FakeSource`** (`kind = "fake"`): generates packets directly at configurable rates. Profiles `usb` (full set, Attitude 100 Hz, Position 50 Hz, VehicleState 20 Hz, FlapState 50 Hz, TvcState 50 Hz, Console text) and `radio` (reduced: Attitude 10 Hz, Position 10 Hz, VehicleState 5 Hz, MissionState 2 Hz, RadioLinkStats 2 Hz, handset `RcState` 10 Hz).
- Scripted, deterministic (seeded) flight: Startup → SensorInit → Ready → Running with mission phases Hover → Ascent → Descent → Landing → Completed. Plausible kinematics (smooth climb to ~5 m, small attitude oscillation and noise, drift), setpoints moving ahead of the vehicle, flaps sweeping between 15° and 90°, TVC thrust following the climb.
- Fault injection: dropouts (gaps of N s for chosen types), rate reduction, a disconnect/reconnect cycle, a NaN value, an unknown enum value, a failure flag set mid-flight.
- Optional test-only echo: `echo_rc = true` copies a configured control into an `applied` field so that the radio round trip can be tested. It never implements vehicle mode logic.
- **`tools/fake_vehicle_pty.py`:** a pseudo-terminal that speaks `provisional-1` bytes (frames plus interleaved console text) driven by the same scenario generator. Used to test `SerialSource`, the framer, the profile, reconnect (closing and reopening the pty) and RC uplink end-to-end.

---

## 7. Technology choices

| Area | Choice | Why |
|---|---|---|
| Language | **Python ≥ 3.11** | `asyncio.TaskGroup`, `asyncio.timeout`, `tomllib` in the standard library, faster interpreter. Available on current Debian/Ubuntu/Fedora/Arch (SteamOS) for x86 and ARM. |
| Concurrency | **asyncio** + a few dedicated threads (§2.3) | One loop is simple to reason about. Threads only where APIs block (serial, fsync). |
| Serial | **pyserial** (≥ 3.5), blocking read in a thread | Mature, pure Python, works on every Linux architecture. Avoids depending on `pyserial-asyncio` maintenance. Handles device loss as an exception cleanly. |
| Web server + WebSocket | **aiohttp** (≥ 3.9) | One dependency for static files, HTTP and WebSocket on the same port, native asyncio. FastAPI/uvicorn would add several dependencies without benefit here. |
| Config | TOML (`tomllib`) for core and radio config, JSON for GUI layout and model config | TOML is readable for hand-edited configs. JSON is native for the browser. |
| Packaging | `pyproject.toml`, `pip install -e .` in a venv; console script `exgs` | `gs` would collide with Ghostscript. No build step for the GUI. |
| Frontend | **Plain ES modules, no bundler**, import map | Nothing to build. It works offline from the core's static files. Any browser with ES modules and WebGL2 (desktop, phone, Steam Frame). |
| 3D | **Three.js 0.170.0**, vendored in `web/vendor/three-0.170.0/` (`three.module.js`, `GLTFLoader`, `OrbitControls`, `Line2` for trails) | Pinned and offline. glTF loader included. |
| Plots | None in v1. Later: uPlot 1.6.31, vendored | Small and fast, if a plot panel is added. |
| Tests | pytest, pytest-asyncio, hypothesis (codec round trips), Playwright for Python (GUI e2e, dev only), `node --test` for pure JS modules (dev only) | Few tools. Node and Playwright are needed only on development machines. |
| Lint/format | ruff (lint + format) | Single tool. |

Dependencies at runtime: `aiohttp`, `pyserial`. Everything else is the standard library.

---

## 8. Repository, testing, docs

### 8.1 Repo layout (`ground-station/`)

```
ground-station/
├── CLAUDE.md                     # project rules (safety review rule §1.4, docs rule, workflow)
├── README.md
├── pyproject.toml
├── config/
│   ├── gs.example.toml
│   ├── radio.pocket.toml
│   ├── layout.default.json
│   └── model.placeholder.json
├── models/                       # user-supplied glTF later (ship.glb, flap.glb)
├── src/exvectr_gs/
│   ├── core/        app.py (wiring, lifecycle), bus.py, state.py, history.py, clock.py, config.py
│   ├── packets/     base.py, fields.py, registry.py, textcodec.py
│   │   └── defs/    vehicle.py (Attitude, Position, Gps, VehicleState, MissionState, FlapState, TvcState),
│   │                link.py (RcState, RadioLinkStats), host.py (LinkStats, ConsoleText, RawFrame, GsEvent, Note)
│   ├── wire/        profile.py (Frame, Framer, WireProfile protocols), provisional_1/ (framer.py, layouts.py)
│   ├── sources/     base.py, serial_source.py, fake_source.py, scenario.py
│   ├── recording/   recorder.py, naming.py, logreader.py, library.py
│   ├── replay/      replayer.py
│   ├── rc/          radio_config.py, controller.py
│   ├── api/         server.py, hub.py, protocol.py (message dataclasses and validation)
│   └── cli/         main.py
├── web/
│   ├── index.html                # import map, shell
│   ├── app/         main.js, ws.js, store.js, clock.js, fmt.js, layout.js
│   │   ├── panels/  registry.js, modes.js, mission.js, failures.js, position.js, guidance.js, actuators.js,
│   │   │            sensors.js, link.js, recording.js, console.js, inspector.js, radio.js, view3d.js
│   │   ├── view3d/  frames.js, scene.js, model.js, placeholder.js, trail.js, markers.js
│   │   └── radio/   pocket.svg, radio_widget.js
│   ├── styles/
│   └── vendor/      three-0.170.0/ (with LICENSE)
├── tools/           fake_vehicle_pty.py, make_sample_logs.py
├── tests/
│   ├── unit/        codecs, registry, state store, recorder, naming, log reader/index, rc controller, framer, profile
│   ├── integration/ core + fake source + API, serial via pty, replay seek
│   ├── web_unit/    frames.test.js, fmt.test.js (node --test)
│   ├── e2e/         Playwright against core in replay mode
│   └── data/        sample .exlog files (generated, small)
└── docs/            (§8.3)
```

### 8.2 Testing strategy

- **Codec round trip (unit, property-based):** for every registered type, hypothesis-generated packets (including NaN, ±inf, None, extreme ints, unknown enum values, Unicode strings) satisfy `decode(encode(p)) == p` (NaN-aware equality). Golden-file test: a checked-in `.exlog` decodes to known values (catches accidental format changes).
- **Wire profile (unit):** byte vectors for each legacy struct built from the documented layouts (e.g. a quaternion of `[1,0,0,0]` → `[32767,0,0,0]`), saturation, the GPS radians quirk. Framer: split and merged reads, garbage between frames, CRC errors, console text interleaving, resync.
- **State store:** freshness transitions with a fake clock, EWMA interval, preferred-source fallback.
- **Recorder:** never overwrites (pre-created files, same-second collisions), header/type/end lines, a crash simulation (kill a subprocess mid-write → the file is readable up to the last complete line, at most 100 ms lost), a restart never auto-records or touches the old file (a new start creates a new file), disk-full error path (a small tmpfs or a mocked write).
- **Replayer:** seek correctness (the state after a seek equals the state after a full sequential replay to the same t), speed, end-of-log, incomplete logs.
- **RC controller:** no sending without a lease; lease expiry stops sending within one period; heartbeat loss; momentary auto-release; defaults after start; send rate ±5 %; every `control` source receives and mirror/none never (several sources, one refusing does not stop the rest); master/slave derivation from handset freshness; master allowed while the Pocket is active, with the `pocket_active` warning flag; `control_hosts` denial.
- **Integration:** core with `FakeSource` + a WebSocket client: subscribe/snapshot/packets, `latest` coalescing at a high rate, slow-client dropping without core stalls. Serial via `fake_vehicle_pty.py`, including reconnect.
- **GUI unit (`node --test`):** `frames.js` conversions (§5.5 test cases), staleness formatting, radio µs ↔ position mapping.
- **GUI e2e (Playwright, replay-based):** start the core on a generated sample log, open the GUI, open the log, seek to known times, and assert panel values, stale styling after a dropout in the log, the default 45° flaps in a log without `FlapState`, and the flap rotation and ship orientation via a debug hook `window.__gs.debug()` that returns scene object transforms. Live-mode e2e with `FakeSource` for the radio widget (take control, click SA, the core reports the sent state).
- **Performance check:** `FakeSource` at 3000 packets/s for 10 min: loop lag < 20 ms, recorder keeps up, the GUI stays at ≥ 30 fps on a mid-range laptop.

### 8.3 Docs plan (`ground-station/docs/`)

| File | Content |
|---|---|
| `README.md` | Index and quick start (install, run with the fake source, open the GUI). |
| `architecture.md` | This document's §1–§3 and §6, kept current. |
| `packet-types.md` | **Generated** by `exgs schema --markdown`. CI/test checks that it is up to date. |
| `log-format.md` | §3.3–§3.5, versioning and migration. |
| `api.md` | §4 WebSocket protocol and CLI reference. |
| `gui.md` | Layout, panel registry, how to add a panel, 3D frames and model config, replay UI. |
| `radio.md` | Virtual radio modes, radio config, lease rules, safety notes. |
| `wire-profiles.md` | How to write a profile; the `provisional-1` definition. |
| `operations.md` | Field host setup (offline install, udev rules, dialout group, systemd user unit, LAN access, disk space), pre-flight checklist (recording started, sources connected, radio in mirroring mode). |
| `testing.md` | How to run each test layer, fake source scenarios. |

Docs are versioned with the code and updated in the same branch (workspace rule). The git workflow is the same as the flight software's (`master` / `Development` / `feature/<name>`).

---

## 9. Implementation plan

Prerequisite (main session, per workspace `CLAUDE.md` "Adding a project"): create `ground-station/` with `git init`, `master` and `Development`, the GitHub repo, the `.gitignore` entry and `.claude/agents/ground-station-dev.md`. Tasks run as `feature/<name>` branches. Parallel tasks in this one repo need worktree isolation.

```
T1 ──┬── T2 ──── T4 ──┐
     ├── T3 ──┬── T7 ─┼── T9 ── T10
     ├── T5   ├── T8 ─┘
     └── T6 ──┘ (T6 API wiring after T3)
```

### T1: Skeleton, interfaces, packet model, text codec, fake source
- **Goal:** a runnable core skeleton that later tasks build on in parallel.
- **Inputs:** this document §2.4, §2.5, §3.1–§3.4, §3.7, §6.5.
- **Outputs:** repo layout (§8.1), `pyproject.toml` (`exgs` entry point), `CLAUDE.md`, docs skeleton; `packets/` (FieldSpec, Packet, `@packet`, registry, GenericTextCodec, all §3.2 types); `core/` (Bus, StateStore, History, clock, config loading and validation); abstract `Source`, `WireProfile`, `Framer` protocols; `FakeSource` with the scenario generator and the `usb`/`radio` profiles; `exgs core` that wires FakeSource → LiveBus → StateStore and prints a 1 Hz summary; `exgs schema [--markdown]`; interface stubs for Recorder, Replayer, RcController and ApiServer (signatures exactly as in §2.4, raising `NotImplementedError`).
- **Acceptance:** codec property tests and golden test pass for all types; state store freshness tests pass; `exgs core --config config/gs.example.toml` runs with the fake source; `exgs schema --markdown` generates `docs/packet-types.md`; ruff clean; docs README and architecture skeleton written.
- **Depends on:** nothing.

### T2: Recorder, log reader, index, library
- **Goal:** crash-tolerant recording and efficient log reading.
- **Inputs:** §3.3–§3.5, T1 interfaces.
- **Outputs:** `recording/` (Recorder with writer thread, naming, LogReader with truncated-line handling and `build_index` with keyframes and history preload, LogLibrary); `exgs log validate|stats|cat` (offline); `tools/make_sample_logs.py` (generates logs from FakeSource scenarios, including one without `FlapState` and one with dropouts); `docs/log-format.md`.
- **Acceptance:** recorder tests in §8.2 pass (never overwrites, crash loses ≤ 100 ms, a new start after a crash makes a new file and leaves the old one untouched, disk-full path); the index of a 1 h synthetic log builds in < 10 s on a laptop; `LogLibrary.list()` reports incomplete files correctly.
- **Depends on:** T1.

### T3: API server, hub and read-side CLI
- **Goal:** the WebSocket protocol, static file serving and CLI.
- **Inputs:** §4, §2.3 (backpressure), T1.
- **Outputs:** `api/` (aiohttp app, hub with per-client subscriptions, `latest` coalescing, bounded `all` queues with `dropped`, `snapshot`, `status`, `event`, `config.get`, JSON time conversion); protocol message validation; CLI commands `status`, `schema`, `watch`, `get`, `wait`, `rec ...`, `logs`, `replay ...`, `sources ...` (the rec/replay ops call the T2/T4 interfaces and return `not_implemented` until those land); a minimal `web/index.html` that shows the raw message stream (replaced by T7); `docs/api.md`.
- **Acceptance:** integration tests with FakeSource (subscribe/snapshot/packets, coalescing at 3000 packets/s, a slow client does not stall the loop: lag < 20 ms); `exgs wait` works on fake data; times in JSON are float seconds.
- **Depends on:** T1. Parallel with T2, T5, T6.

### T4: Replayer
- **Goal:** replay view with play/pause/seek/speed.
- **Inputs:** §5.7 (seek implementation), §6.4, T2 LogReader/index.
- **Outputs:** `replay/replayer.py`, ReplayBus/StateStore/History wiring in `core/app.py`, `replay.*` and `logs.list` API ops, history queries for the replay view.
- **Acceptance:** the state after a seek equals sequential replay (property test over random seek times); seek < 200 ms for a 1 h log; speeds 0.25–8×; `ended` state; recording continues during replay (integration test).
- **Depends on:** T2, T3.

### T5: Serial source and provisional wire profile
- **Goal:** real serial I/O with reconnect, and the wire plug-in path.
- **Inputs:** §3.6, §6.1–§6.3, legacy layouts in `flight-software/docs/project/hardware-and-drivers.md` §9 and `flight-software/docs/libs/ExVectrPackets.md`.
- **Outputs:** `sources/serial_source.py` (device matching, DTR/RTS handling, reader and writer threads, reconnect state machine, LinkStats); `wire/provisional_1/` (COBS framer with CRC16, console text passthrough, layouts for ports 10/11/12/50/51 with documented scaling and quirks, `RcState` encode); `tools/fake_vehicle_pty.py`; source priority and two-source handling; `docs/wire-profiles.md`; udev rules in `docs/operations.md`.
- **Acceptance:** layout byte-vector tests and framer robustness tests pass; end-to-end over the pty (packets decoded, console text appears as `ConsoleText`, unknown channels appear as `RawFrame`); unplug simulation (pty closed and reopened) → `searching` → `connected`, `reconnects` incremented; two simultaneous pty sources work with priorities.
- **Depends on:** T1. Parallel with T2, T3, T6.

### T6: RC controller
- **Goal:** the virtual radio's core side, built to the safety rules.
- **Inputs:** §1.4, §4.2 (`rc.*`), §5.6 (config, modes, momentary), §2.5 (`control_hosts`).
- **Outputs:** `rc/radio_config.py` (TOML schema and validation), `rc/controller.py` (leases connection/keepalive (the first version had a timed lease, replaced by decision 10.1-7), control locks (10.1-6), heartbeat, fixed-rate sender to the control source only, momentary auto-release, slave detection (handset freshness), master/slave status, GS `RcState` recording on change + 1 Hz, no persistence); `rc.*` API ops and `exgs rc ...` CLI (after T3 lands; until then a core-level test harness); `config/radio.pocket.toml`; `docs/radio.md`.
- **Acceptance:** all RC tests in §8.2 pass; with the pty fake vehicle the uplink frames arrive at `send_rate_hz` ±5 % and stop within one period after release or lease expiry; a core restart starts with defaults and sends nothing.
- **Depends on:** T1 (core part); T3 (API/CLI wiring). **Safety-relevant: the main session reviews it against §1.4 and the user approves it before merge.**

### T7: GUI shell and data panels
- **Goal:** the browser application except the 3D view and the radio widget.
- **Inputs:** §5.1–§5.4, §5.7, §4.
- **Outputs:** `web/` shell (ws client with reconnect, per-view store, view clock, `fmt` with staleness, panel registry, layout from `layout.json` with presets and collapse), panels: modes, mission, failures, position, guidance, actuators, sensors, link, recording/replay (log list, transport bar, record controls, notes), console, inspector; `docs/gui.md` (incl. "adding a panel").
- **Acceptance:** with FakeSource every panel shows values with source tags; stopping a type in the fake scenario turns its values stale with an age; LIVE/REPLAY switch works with T4 (or the T2 sample logs once T4 lands); phone preset usable at 390 px width; `node --test` passes for `fmt.js`.
- **Depends on:** T3 (T4 for the replay controls to be fully functional).

### T8: 3D view
- **Goal:** ship, flaps, trail, markers, with correct frames.
- **Inputs:** §5.5, `config/model.placeholder.json`.
- **Outputs:** `web/app/view3d/` (frames.js, scene, config-driven model loader supporting `primitive` and `gltf` sources, procedural placeholder ship and flaps, flap pivots/axes/sign/model angle, default 45°, trail from history + live, setpoint marker and velocity arrow, thrust arrow, camera modes, stale rendering), the `view3d` panel; a `debug()` hook for e2e; model config section in `docs/gui.md`.
- **Acceptance:** `frames.test.js` cases pass (§5.5); a test glTF (simple box exported with known axes) loads through the same path as the placeholder; flaps follow `FlapState` and stay at 45° without it; the trail rebuilds after a replay seek.
- **Depends on:** T3 (protocol), T7's panel registry (fixed in §5.3, so it can start in parallel against that interface and integrate after T7).

### T9: Virtual radio widget
- **Goal:** the Pocket-like widget for master, slave, idle and replay.
- **Inputs:** §5.6, T6 API.
- **Outputs:** `web/app/radio/pocket.svg`, `radio_widget.js`, the `radio` panel (config-driven controls, take/release control, heartbeat, momentary press-and-hold, slider throttling, mode banners, read-only states, padlock for locked controls, replay behaviour with the release prompt).
- **Acceptance:** e2e with FakeSource (`echo_rc`): take control, operate each control kind, the core-reported sent state is displayed; closing the tab stops sending within 1 s; mirroring with the fake `radio` profile shows the handset positions read-only; switching to replay while controlling prompts and releases.
- **Depends on:** T6, T7. **Safety-adjacent: reviewed together with T6.**

### T10: End-to-end tests, operations docs, hardening
- **Goal:** release readiness for bench use.
- **Inputs:** everything above.
- **Outputs:** Playwright replay-based e2e suite (§8.2), performance check, `docs/operations.md` (offline install, systemd user unit, LAN access, pre-flight checklist), `docs/testing.md`, a review pass that the docs match the code.
- **Acceptance:** the full test suite passes; a 10 min run at 3000 packets/s meets the performance criteria; a fresh install on a second Linux machine (or a container) following `operations.md` works offline.
- **Depends on:** T1–T9.

---

## 10. Open questions and risks

### 10.1 Decisions (user, 2026-11-18 and 2026-11-19)

1. **RC sending when the controlling client disappears:** the core **stops sending** (GUI heartbeat lost for 1 s, or a CLI lease expired). The vehicle sees silence and enters its link-loss failsafe (safety.md 2.4). It never keeps sending the last state.
2. **Raw bytes in recordings:** only undecodable frames are kept (`RawFrame`). No option to record every frame's raw bytes (`raw_frames = "all"` is dropped).
3. **Recording start:** manual Start/Stop only. There is no auto-start.
4. **Who may take control:** only clients on the core host itself (`control_hosts = localhost`). Other LAN devices are view-only. A **CLI on the core host** can take control so that an AI agent can drive simulation tests (a keepalive lease or `exgs rc session`, see 7). The CLI follows the same lease and stop-on-expiry rules as the GUI.
5. **Interim USB format:** build against the provisional framer and the fake vehicle. Keep a strict abstraction layer between source, framer/wire profile, packet registry and everything above, so each can be replaced later without touching the rest. Real hardware comes after roadmap step 3.
6. **Lock on guarded controls (user, 2026-11-19):** the ground station can send SD (motor enable) but it sits behind a deliberate lock so it cannot be set by accident. This is a deliberate-action guard, not a safety decision; the vehicle's own 1 s motor gate is unchanged. The ground station does not know what SD means, so the lock is a generic per-control option in the radio config (`lock = true`, `lock_timeout_s = 60`), set on SD. A locked control is held at its default; setting a non-default value while locked is rejected (`control_locked`) and applies nothing. `rc.unlock {control}` (needs a held lease) opens it for `lock_timeout_s`, then it relocks and returns to its default (off); `rc.lock` relocks at once. Any lease end, control-source drop, `acquire` and core start returns all controls to their defaults and relocks. GUI: padlock, hold 1 s, remaining time, relock button. CLI: a non-default value needs `--unlock SD`. The lock state is part of the RC status.
7. **Lease rework (user, 2026-11-19):** the 600 s timed lease is removed. If nobody is actively in control, sending stops within single-digit seconds. The GUI/connection lease is unchanged (heartbeat at least every 1 s, WebSocket close ends it at once). The CLI has two ways: (1) `exgs rc session`, a long-running process holding a connection lease with a 300 ms heartbeat, reading commands from stdin or a script file (crash or exit ends it at once, heartbeat loss within 1 s); (2) one-shot `exgs rc ...` commands take or renew a **keepalive lease** with an idle timeout of 5 s (configurable, max 9 s), renewed by every rc command and by `exgs rc keepalive`; expiry means silence and defaults, `exgs rc release` ends it at once. Expiry is checked on every tick and before every operation, so a late renewal cannot revive an expired lease. Decision 10.1-1 and the "CLI can take a timed lease" part of 10.1-4 are superseded accordingly.
8. **RC master/slave (user, 2026-11-19):** "If the ground station receives an RC packet (from the Pocket), then it goes into slave mode and simply shows the current settings of the Pocket. I can set it to master and then it starts sending the controls to the serial port. The Pocket should always reject them." Implemented: **slave** is the automatic state while a fresh handset `RcState` arrives and nobody is master (read-only display, nothing sent); **master** is the explicit user switch (the control lease: GUI "Switch to master", `exgs rc acquire` / `session` / keepalive), allowed while the Pocket is active, with a "Pocket also active: the radio rejects ground-station controls" warning (display only). Master sends to every connected `rc_role = "control"` source, including the TX-module USB link (shipped config: usb and link are both `control`; `mirror` stays as a per-source receive-only option). Everything else is unchanged (25 Hz sender, defaults on every start, SD lock, lease timing, silence when the lease ends, replay never sends, nothing persisted). The rejection happens in the TX module (it drops `RC_STATE` from USB towards the air) and later the vehicle, never in the GS; this removes the GS's own refusal to send through the TX module as a defence layer. `RcStatus.mode` is now `master`/`slave`/`idle` (the old `controlling`/`mirroring`/`conflict` are gone) with `targets` and `pocket_active`. Supersedes the "control source only" rule in 1.3 and the single-control-source constraint.
9. **Whitelisted REQUEST sender (user, 2026-11-19):** "we still want to be able to send things like calibration commands". The uplink rule is now: the ground station sends only (a) RC state under the lease rules and (b) `REQUEST`s from a configured whitelist (`[requests]` in `gs.toml`: action, label, group, target node, confirm level). Still no raw-send API, nothing automatic, never in replay, never from a source that is not live. Idempotent ids, retry every 1 s up to 5 attempts, stop on the ACK (workspace network-design 3.5, with the user's retry timing instead of 250 ms x 10). Deliberate confirmation: GUI hold 1 s (plus a modal with a 2 s disabled confirm button for `danger`), CLI `--confirm ACTION` (plus `--i-understand-the-link-stops` for `danger`), no default. `LINK_UPLOAD_MODE` is `danger`: the TX/RX module always obeys it (safety.md 2.5 exception) and the link stops. The GS shows the vehicle's ACK status exactly, never an assumed success. Details: `docs/requests.md`.
10. **TEMPORARY legacy telecommand uplink (user, 2026-11-20):** a third uplink, besides RC state and the whitelisted REQUESTs, approved explicitly and **to be deleted when the flight software's T9b** replaces it by `RC_STATE` mapping and `REQUEST`s. The vehicle accepts the legacy 8-byte `Telecommand` on ExVectrNetwork port 0x7F (src node 0, dst node 1; flight-software `docs/project/hardware-and-drivers.md` section 10). Whitelist in `[telecommands]` (arm, disarm, system reset, simulation toggle, mission begin and abort, magnetometer calibration), each with confirm level `hold` (GUI hold 1 s, CLI `--confirm NAME`), control hosts only, only to a connected route (vehicle USB first, else the Router), never automatic, never in replay, 500 ms per-client rate limit, **retry none: one frame per confirmation** (the vehicle de-duplicates nothing; `SimulationMode` toggles), result only from what the vehicle reports (`VehicleStatus.simulation`, never an assumed state). See [telecommands.md](telecommands.md). Same date, user decisions: a mission status bar (vehicle-reported values only), user-facing renames (node 2 = Router, node 3 = ship radio node, [glossary.md](glossary.md)), `LINK_REBOOT` in the request whitelist, the schema pin with `LINK_CONFIG` shown on the link panel, flaps with the chamfer on top, and a raw sensor panel with the calibrated column computed from `CALIBRATION` (display only; schema pin `9674f89`, hash 0xCFB7AEED).

### 10.2 Risks and mitigations

| # | Risk | Mitigation |
|---|---|---|
| R1 | Wire format redesign (framing, ports, layouts, RC state packet) | Everything binary lives in one `WireProfile` package (§3.6). Logs are semantic text and stay readable. `RawFrame` keeps unknown data for later re-decoding. The registry is designed so that it can later be generated from the shared protocol library. |
| R2 | The semantic packet set changes (types split, renamed, new units) | Versioned types with `migrate` hooks, tolerant decoding (unknown fields kept, unknown types preserved), self-describing `#type` lines in every log, and the Inspector panel showing new types immediately. |
| R3 | High USB rate overloads the GUI or the network | `latest` coalescing at ≤ 30 Hz per client, bounded `all` queues with drop counters, history downsampled to 20 Hz. The recorder runs on its own thread. Performance test in T10. |
| R4 | The ground station drifts into control logic over time | Review rule in the GS `CLAUDE.md` (§1.4); outbound whitelist by `direction`; no raw-send API; T6/T9 marked safety-relevant. |
| R5 | Radio config labels do not match the vehicle's compiled-in channel table | Labels are informational and the config carries `mapping_note`. Proposed later vehicle telemetry: a mapping revision/hash, so the GUI can warn on mismatch (a cross-project change, to be decided in roadmap step 3). |
| R6 | Body axes and flap placement (which flap is TL/TR/BL/BR relative to body X/Y) are not documented precisely enough for the default model config | Everything is configurable in the model config. The flight-software agent should document the body frame and flap/vane positions in `hardware-and-drivers.md` before T8 finalises the placeholder config. |
| R7 | Today's telemetry has unit quirks (GPS in radians, variances or unitless accuracies, int16 overflow of ground distance) | `provisional-1` decodes the actual behaviour and the field descriptions document it. The real fix belongs to the flight software and protocol redesign. |
| R8 | Opening the TX module's port resets the ESP32 (DTR/RTS) | DTR/RTS deasserted before open (§6.1), tested on hardware once the USB link exists. |
| R9 | Clock and time issues (NTP steps, JavaScript integer precision, remote device clocks) | Monotonic-anchored host time, float seconds in JSON, server-provided view clock (§2.3, §4.1, §4.3). |
| R10 | Field host differences (ARM, SteamOS, no internet) | Two pure-Python runtime dependencies, vendored frontend, nothing GPU-specific in the core, offline install procedure in `operations.md`, install test in T10. |
