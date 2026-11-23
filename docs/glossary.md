# Glossary (user-facing names)

User decision 2026-11-20. These are the names the GUI, the CLI, the config labels and the docs use. Packet type names and code identifiers keep their old spelling (`TxModuleStats`, `RxModuleStats`, `link_module`, `txmodule` in the test double) because renaming them would break logs and the schema without helping the operator.

| Say | Meaning | Node id | Old name (code, packet and log identifiers only) |
|---|---|---|---|
| **vehicle** | The Starship flight computer (Teensy 4.0), directly on USB or over the radio. | 1 | vehicle |
| **Router** | The ExVectrLink module in the Radiomaster Pocket (ESP32-S3, SX1280). It is the ground end of the radio link, forwards the vehicle's radio set and the Pocket's `RC_STATE`, and is the configuration master of the link (`LINK_CONFIG`). Source kind `link_module`. | 2 | TX module (`TxModuleStats`, `role="txmodule"`) |
| **ship radio node** | The ExVectrLink receiver module on the vehicle. It is a separate node and has its own `LINK_STATS`. | 3 | RX module (`RxModuleStats`) |
| **ground station** | This software; node 0. | 0 | |
| **Pocket** | The Radiomaster Pocket handset (the person's RC input). Its `RC_STATE` goes to the ground station over the Router's USB only; the **command engine** turns its switches into the command state. | | |
| **Command engine** | The core's sender of `CMD_STATE` (25 Hz, session id, pickup rule). The ground station is the vehicle's only control source. SD is "motors armed / disarmed". | | |
| **vehicle USB** | A `vehicle_usb` source: the Teensy's own USB serial port (peer node 1, the full debug set). | | |
| **Router link** | The `link_module` source, i.e. the Router's USB serial port; reaches nodes 1 (over the air), 2 and 3. | | |

Other terms: **REQUEST** is the whitelisted one-shot request with an ACK ([requests.md](requests.md)). **Telecommand** is the TEMPORARY legacy 8-byte command on port 0x7F ([telecommands.md](telecommands.md)). **Master** and **slave** are the RC modes of the ground station ([radio.md](radio.md)).
