# REQUEST sender (whitelisted one-shot actions)

Spec: [ground-station-design.md](ground-station-design.md) sections 1.4 (rule 2.5) and decision 10.1-9; workspace `docs/network-design.md` 3.5 / 4.7.2 / 4.7.3 and the "Revision 1" box; workspace `docs/safety.md` section 2 rule 5. Code: `src/exvectr_gs/requests/controller.py` (`RequestController`), config in `core/config.py` (`[requests]`), API ops in `api/server.py`, CLI `cli/cmd_request.py`, GUI `web/app/requests/` (panel `requests`).

**Safety-relevant.** User decision 2026-11-19: "we still want to be able to send things like calibration commands". This **widens the uplink beyond RC state**. The ground station sends exactly these things (a third, TEMPORARY, is described in [telecommands.md](telecommands.md)): (a) RC state under the lease rules ([radio.md](radio.md)) and (b) whitelisted events and `REQUEST`s after operator confirmation (commands: `hold`; link-module actions: `danger`) and (c) automatic requests only where a setting enables them: `[requests] auto_home` (default true), home only, on the T-5 trigger (workspace reliable-transport-design 8.1). There is still no raw-send API; nothing else is automatic; nothing is ever sent from replay or without a live source; results are display data only. Retries are done by the transport, not decided by the GS. Rule wording approved 2026-11-21 (reliable-transport-design 7.4); `auto_home` and the new transports are not implemented yet, the text below describes the current confirmed-only request sender. The ground station makes no decision for the vehicle: it does not interpret an action and it never assumes an outcome.

## What a request is

`REQUEST` (port 0x11, QoS EVENT) carries `req_id` (u16, non-zero), `action` (u8, the schema's `RequestAction`), `target` (u8 node id: 1 vehicle, 2 Router, 3 ship radio node), `arg_i` and `arg_f` (both 0 here). The vehicle answers with `REQUEST_ACK` (port 0x12): `req_id`, `status` (0 DONE, 1 IN_PROGRESS, 2 REJECTED_STATE, 3 REJECTED_INVALID, 4 FAILED) and `detail`. The vehicle accepts a request only while disarmed and on the ground (safety.md 2.5); that is **the vehicle's decision**, the ground station neither checks nor assumes it, and shows the status exactly as reported.

**The exception.** `LINK_UPLOAD_MODE` (action 10) with target 2 or 3 is always honoured by the link module, in flight too: the module stops its radio link and enters over-the-air firmware upload (safety.md 2.5, network-design Revision 1). A module cannot know the vehicle state, so the sender is the guard. That is why the ground station treats it as confirm level `danger`.

## The whitelist (`[requests]` in `gs.toml`)

```toml
[requests]
source_preference = ["usb", "link", "fake"]    # optional, see "Routing"

[[requests.action]]
action = "CALIB_APPLY"          # name of the schema's RequestAction (not NONE)
label  = "Apply calibration"    # shown in the GUI and `exgs request list`
group  = "Calibration"          # GUI panel grouping
target = 1                      # 1 vehicle, 2 Router, 3 ship radio node
confirm = "hold"                # "hold" (default) or "danger"
warning = ""                    # optional: the text of the danger modal and the CLI refusal
```

Shipped in `config/gs.example.toml`: `CALIB_GYRO_START/STOP`, `CALIB_ACCEL_START/STOP`, `CALIB_MAG_START/STOP`, `CALIB_APPLY`, `SAVE_CONFIG`, `SYSTEM_RESET` (all target 1) and `LINK_UPLOAD_MODE` and `LINK_REBOOT` (action 11) for target 2 (Router) and target 3 (ship radio node), all `confirm = "danger"`; labels `Router: firmware upload mode`, `Ship radio node: firmware upload mode`, `Router: reboot`, `Ship radio node: reboot`. `LINK_REBOOT` restarts the module and interrupts the radio link for seconds: **a reboot of the ship radio node in flight interrupts RC to the vehicle for seconds** (its `warning` says so in the modal and in the CLI refusal; the module always obeys). `RequestAction` mirrors the schema pinned at `a96bd7b` (see [wire-profiles.md](wire-profiles.md)). An entry is identified by (action, target): the same action may appear for several targets. Validation (`ConfigError`, the core does not start): unknown action name or `NONE`, target not 1..3, `confirm` not `hold`/`danger`, duplicate (action, target), unknown keys, unknown source ids in `source_preference`. No `[requests]` section means an empty whitelist: nothing can be sent. `RequestAction` and `RequestStatus` in `packets/defs/link.py` mirror the generated schema enums (a test compares them, so a schema change that adds or renumbers an action fails the test until the mirror is updated).

Enforcement in the core (`RequestController.send`): an unknown action, an action sent for a target that is not whitelisted for it, or an ambiguous target (action whitelisted for several targets and none given) is refused with `not_whitelisted` before anything touches a source. There is no way to name an action number.

## Ids, retry, one in flight

- `req_id` is a non-zero u16, **new per action** (monotonic, wrapping, 0 skipped, never an id still in flight) and **reused for every retry** of that action, so the vehicle executes it once (it remembers the last 8 `(src, req_id)`). The counter starts at a **random** value at core start: a restarted ground station counting from 1 again could have a fresh action taken for a repeat of a remembered id and never executed.
- **Retry** (user timing, replaces the 250 ms x 10 of network-design 3.5): attempt 1 at once, then one attempt per second, at most **5 attempts**. Retries stop **at once** on a matching `REQUEST_ACK` (any status, `IN_PROGRESS` included: the vehicle has it). One second after the fifth attempt without an ACK the request ends as `timeout`. Each attempt is routed afresh (see below); an attempt with no connected source is counted and not queued.
- **One in flight per (action, target).** A new send of the same pair while it is in flight is refused (`request_in_flight`). Different actions, or the same action for another target, may be in flight together.
- A **later ACK** of a request that already finished (for example `DONE` after `IN_PROGRESS`) updates its entry in the recent list.
- **State shown is from ACKs only:** `sending` (attempt n of 5), `acked` with the status name exactly as received (`DONE`, `IN_PROGRESS`, `REJECTED_STATE`, `REJECTED_INVALID`, `FAILED`, or `#n` for a value the mirror does not know, shown as not-OK), or `timeout`. The GUI and the CLI never show a request as done because it was sent. The last 20 finished requests are kept in memory only; nothing is persisted.
- **Recording:** requests and ACKs are recorded as `GsEvent`s (kinds `request_sent`, `request_retry`, `request_ack`, `request_timeout`, with action, target, id, source, attempts and status in the detail), and the vehicle's `RequestAck` packets are recorded like any received packet. No request state is ever saved and replay never sends anything.

## Routing

dst = the entry's `target`. A request goes out on the **first connected serial source, in preference order, that can reach the target**: the vehicle's USB (`vehicle_usb`) reaches node 1 only, the Router (`link_module`) reaches node 1 over the air, 2 and 3. So target 1 uses the vehicle USB when it is connected and otherwise the Router link (air); targets 2 and 3 only ever use the Router link. The order is the documented config list `[requests] source_preference` (source ids, first wins); not set, it is every `vehicle_usb` source, then every `link_module` source, then fake sources, each in config order. A source not in the list is never used for requests. `rc_role` is irrelevant: requests also go out on a `mirror` or `none` source, because they are not RC state. A source reports what it can reach (`SourceCaps.request_targets`); `SerialSource.send` refuses a request for a target outside its reach and every non-`UP` packet type.

If no suitable source is connected, or none accepts the frame, the send is **refused with `no_route`** and a message naming the target. **Nothing is queued** for later and reconnecting a source sends nothing. The frame goes through the wire profile (`encode` of `exvectrnet-1`, [wire-profiles.md](wire-profiles.md)); `provisional-1` cannot encode a request, so with that profile every send is refused.

## Deliberate confirmation

| Where | Rule |
|---|---|
| GUI | Requests panel: every button needs a **hold of 1 s** (progress bar; releasing early sends nothing). A `danger` action then opens a modal: the row's own `warning` (for `LINK_REBOOT`: "Reboots the ship radio node: RC to the vehicle is interrupted for seconds, in flight too. The module always obeys."), else the generic "This acts on a link module (the Router or the ship radio node): its radio link stops or restarts. The module always obeys, even in flight." Its confirm button is **disabled for 2 s** (countdown in its label). Cancel or Escape close it without sending. Nothing in the GUI sends a request by itself (the only automatic request is the enabled `auto_home`, not yet implemented). The panel is hidden in the replay view and its buttons are disabled in the replay view, without a connection, on a view-only host, while the same request is in flight, and when no source can reach the target. |
| CLI | `exgs request send ACTION [--target N] --confirm ACTION` (the action name repeated; no default) and, for a `danger` action (`LINK_UPLOAD_MODE`, `LINK_REBOOT`), also `--i-understand-the-link-stops` (the refusal prints the action's warning). The CLI checks both before it sends anything; the core checks them again. |
| Core | `request.send` takes `confirm` (must equal the action name) and, for `danger` entries, `understand_link_stops: true`; otherwise `confirm_required`. A client that skips its own guard is still refused. |

Beyond confirmation: only **control hosts** (`server.control_hosts`, the same as RC control) may send, checked in the API (`control_denied`) and again in the controller from the peer address; and at most **one new request per 500 ms per client host** (`rate_limited`; keyed by peer address, so reconnecting does not reset it; a refused send does not count).

## API and CLI

`request.send {action, target?, confirm, understand_link_stops?}` replies with the new request state (`state: "sending"`, `req_id`, `attempts`, `source`, ...). `request.list` replies `{actions: [{action, target, target_name, label, group, confirm, route}]}` (`route` is the id of the source a send would use now, display only). `request.status` replies `{inflight: [...], recent: [...]}` (newest first). The core pushes `{"op": "requests", inflight, recent}` after hello and on every change (send, retry, ACK, timeout); the 2 Hz `status` push carries `request_routes` (target node to source id). Error codes are in [api.md](api.md). CLI:

```bash
exgs request list
exgs request status
exgs request send CALIB_APPLY --confirm CALIB_APPLY                    # waits for the ACK (about 5 s at most)
exgs request send LINK_UPLOAD_MODE --target 2 --confirm LINK_UPLOAD_MODE --i-understand-the-link-stops
exgs request send LINK_REBOOT --target 3 --confirm LINK_REBOOT --i-understand-the-link-stops   # ship radio node: RC interrupted for seconds
```

`send` waits for the final state and prints it (`... ACK DONE (detail 0) after 1 attempt(s)`, or `TIMEOUT, no REQUEST_ACK after 5 attempts`). Exit codes: 0 for `DONE` and `IN_PROGRESS`, 1 for any other status or error, 2 for a timeout, 3 control denied. `--no-wait` returns after the first attempt.

## Test doubles (no vehicle logic)

- `FakeSource` option `answer_requests = true`: accepts `Request` packets for the targets in `request_targets` (default 1, 2, 3) and answers each with a `RequestAck` of `request_status` (default 0 DONE; 2 gives REJECTED_STATE for tests) and `request_detail`; it executes each `req_id` once (`executed_requests`), answers repeats again, and `request_drop_first = N` swallows the first N attempts of every id (a large N never answers: timeout). `received_requests` lists every attempt. The shipped example config enables it on the fake source so the GUI demo works.
- The pty fake vehicle (`tools/fake_vehicle_pty.py`, `exvectrnet-1`): a REQUEST addressed to its own node (1 as `vehicle`, 2 as `txmodule`) is answered with a `REQUEST_ACK` frame (`request_status`, `request_drop_first`, also `--request-status` and `--request-drop-first` on its command line); requests for other nodes are only recorded and forwarded. `received_requests` holds the parsed frames. These are test doubles: the real acceptance rules (disarmed and on the ground, idempotence) are the vehicle's.
