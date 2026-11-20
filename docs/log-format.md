# Log file format (design sections 3.3-3.5)

Spec: [ground-station-design.md](ground-station-design.md). Code: `exvectr_gs.recording` (`recorder.py`, `logfile.py`), the line codec is `exvectr_gs.packets.textcodec`.

A log is a UTF-8 text file, `\n` line endings, append-only, one packet per line. It is semantic (types and fields, not wire bytes), so it stays readable after the wire format changes. Extension `.exlog`; `.exlog.gz` is also read (offsets are positions in the uncompressed stream).

## Lines

| Line | Meaning |
|---|---|
| `#exlog v=1` | First line, format version. A file whose first line is not this is not a log. |
| `#meta key=value ...` | Written at start: `gs` (version), `host`, `profile` (wire profile), `config` (hash), `started` (ISO 8601 with local offset, ms), `label` (if given). Values containing spaces, quotes or `=` are JSON-quoted. |
| `#source id=.. kind=.. device=.. rc_role=..` | One per enabled source in the header block, and again on each connect (`Recorder.source_connected`). |
| `#type Name v=N field:kind[:unit] ...` | Before the first packet of each type in the file (arrays as `float[3]`, `int[16]`). Makes the log self-describing. Readers ignore it. |
| `<t> <src> <Type> field=value ...` | A packet. `t` is int ns since the Unix epoch. Value syntax: design 3.4. |
| `#end stopped=<ISO> packets=<n>` | Clean stop. `n` counts packet lines. Missing means the recording was cut off (crash, power loss, still recording). |

Example: `tests/data/golden.exlog` and the output of `tools/make_sample_logs.py`.

## Naming and recording rules

- `<directory>/YYYY-MM-DD_HH-MM-SS[_label].exlog`, local time. The file is created with exclusive create (`open(path, "xb")`), so an existing file is never overwritten. Two starts in the same second get `-2`, `-3`, ... after the stem. In the file name the label has path separators and characters invalid on common file systems replaced by `-` and spaces joined with `-`. The `label` in `#meta` is the original text.
- **Recording starts and stops only by explicit user action.** Nothing starts a recording at core start-up, after a source connects, or after a crash. `Recorder.start()` always creates a new file, `stop()` writes `#end`.
- Recording begins with a `GsEvent rec_start detail="manual"` line and ends with `GsEvent rec_stop` before `#end`. `Recorder.note(text)` publishes a `Note` (recorded while active).
- Recorded: everything published on the live bus (design 3.5). Replay packets never are. Only undecodable frames are kept as `RawFrame`.
- **Durability:** a writer thread flushes every `flush_interval_ms` (100 ms) and fsyncs every `fsync_interval_ms` (1 s) and on stop. A core crash loses at most about 100 ms, an OS crash or power loss at most about 1 s.
- **After a crash** the file stays as it is: valid, no `#end`, possibly a truncated last line. Start a new recording manually.
- **Write error / disk full:** the writer stops that recording, `Recorder.status()` shows `active=False` and `last_error`, the `on_error` callback fires (the API turns it into an error event), the core keeps running. The partial file stays valid. If the writer cannot keep up, packets are dropped and counted (`status().dropped`) rather than blocking the bus.

## Reading

- A final line without `\n` is a truncated write and is ignored by every reader (`iter_packets`, `build_index`, `LogLibrary`, `exgs log`).
- `LogReader.iter_packets(start_offset)` yields `(byte offset, Packet)`, skips `#` lines, and skips undecodable middle lines (they are listed in `reader.bad_lines`). Unknown types come back as `UnknownPacket`, unknown fields stay in `Packet.extra`.
- `LogReader.build_index(keyframe_s=1.0, history_hz=20)` makes one pass and returns a `LogIndex`:
  - `times`, `offsets`: `array('q')` with one entry per packet line (time is a running maximum, so it is sorted). `offset_for(t)` gives the first line at or after `t`.
  - `keyframes`: every `keyframe_s` of log time `(t, offset, latest)` where `offset` is the first line at or after the boundary and `latest` maps `(type, src)` to the offset of the latest earlier line. A gap in the log gets one keyframe. `keyframe_for(t)` returns the last keyframe at or before `t`. To seek: decode the `latest` lines into the state store, then apply lines from `offset` up to `t`.
  - `history`: packets of `history=True` types, at most `history_hz` per `(type, src)`.
  - `markers`: `GsEvent`, `Note` and vehicle mode changes for the scrubber.
  - Speed: about 1 M lines (a 1 h USB log) indexes in about 2 s.
- `LogLibrary(dir).list()` returns `LogInfo` newest first from the header and the tail only (the first 2 MB are scanned for `VehicleState.sim`). `complete` is true when `#end` is present. For an incomplete file `duration_s` comes from its last complete line. Files that do not start with `#exlog` are skipped with a warning.

## Offline tools (no core needed)

```
exgs log validate FILE          # exit 1 on errors (bad lines, wrong #end count, bad header); warnings for no #end, truncated last line, unknown types, time going backwards
exgs log stats FILE [--json]    # per type and source: count, rate, longest gap
exgs log cat FILE [--types A,B] [--src S] [--start SEC] [--end SEC] [--count N] [--meta]   # lines as stored; times relative to the first packet
exgs log import-legacy FILE... [--out DIR] [--manifest PATH]   # old ExVectrController CSV logs -> .exlog, see legacy-logs.md
python tools/make_sample_logs.py OUTDIR [--duration 60] [--only full,no-flapstate,dropouts,radio]
```

Sample scenarios: `full` (USB set), `no-flapstate` (no `FlapState` at all, GUI shows default 45 degree flaps), `dropouts` (Position gap, whole-source disconnect, unknown enum value), `radio` (reduced radio set). `exgs log redecode` (design 3.6) is not implemented yet.
