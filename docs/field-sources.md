# Fields by source

The vehicle's USB telemetry (`usb`, full set, 50 to 100 Hz) and the Router's radio telemetry (`link`, reduced set, 5 to 25 Hz) map to the same ground station packet types, but not to the same fields. A field a packet does not carry is `None` (omitted in the API item). Observed live with `exgs get --src usb|link <Type>` on 2026-11-22.

**Cause of the flicker (confirmed).** The GUI and `exgs get` used one source per type (the freshest packet, or the highest-priority fresh source). With both connected, the chosen packet alternated between `usb` and `link`, and each field that exists in only one set showed its value then `n/s` in turn: `Actuators.roll_torque` (link only), `Actuators.ground_distance` (usb only), `MissionState.mission_mode` (link only), `Attitude.omega` (usb only; the radio `AIR_POSE` has `q` only).

## Matrix (`U` = usb, `L` = link, `-` = neither; mapping in `wire/exvectrnet_1/mapping.py`)

| Type | Field | usb | link | Note |
|---|---|---|---|---|
| Attitude | `q` | U | L | |
| Attitude | `omega` | U | - | radio `AIR_POSE` has no body rates |
| Position | `pos`, `vel` | U | L | |
| Position | `ground_distance` | U | L | moved here from Actuators (schema 0x93EF9C5F); both sets carry it |
| Actuators | `flaps`, `tvc`, `thrust` | U | L | |
| Actuators | `roll_torque` | U | L | gap filled by schema 0x414DE27A (USB `ACTUATORS` carries it; older sender: `None`) |
| FlapState | `angle` | U | L | |
| MissionState | `sp_vel`, `sp_pos`, `sp_q`, `mission_phase`, `t_mission`, `mission_index` | U | L | |
| MissionState | `mission_mode` | U | L | gap filled by schema 0x414DE27A (USB `MISSION` carries it) |
| MissionState | `waypoint` | U | L | USB `MISSION` carries it since 0x414DE27A; only while a waypoint mission is active (255 = none, `None`) |
| VehicleStatus, VehicleState | all | U | L | same wire packet on both |
| Gps | all | U | L | USB `GNSS`; radio `AIR_GNSS` plus `AIR_GNSS_INFO` merged by the profile |
| Estimator, ImuRaw, Calibration | all | U | - | |
| RadioLinkStats | all | - | L | radio only by nature |

## Rules

- Per field: the value of the highest-priority source (`core.source_priority`, default usb, link, fake) whose packet is fresh and carries that field; if no fresh source carries it, the newest packet that does (shown stale, with its own age); otherwise nothing.
- Age, source tag and stale threshold are those of the field's own packet, so a radio-only field shows steadily with the radio's age.
- A field no connected source carries shows `not sent` in the GUI (with two or more sources; with one source `n/s`, as before) and is absent in `exgs get`. No value is invented.
- Where it lives: the core state store (`StateStore.get_merged`) next to the existing `source_priority` logic, so `exgs get` (`get_snapshot preferred`) and the GUI agree; the GUI store (`Store.get` without `src`) applies the same rule over the per-source packets it receives, using the `priority` list in the snapshot. Recording, replay, the inspector and the data rates panel stay per source.
