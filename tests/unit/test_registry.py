import json
from enum import IntEnum

import pytest

from exvectr_gs.packets import REGISTRY, Direction, Packet, PacketRegistry, f, packet
from exvectr_gs.packets.defs.link import RcState
from exvectr_gs.packets.defs.vehicle import Attitude


def test_all_section_3_2_types_registered():
    names = {p.name for p in REGISTRY.all()}
    assert names == {
        "Attitude", "Position", "Gps", "VehicleState", "MissionState", "FlapState", "TvcState", "RcState",
        "RadioLinkStats", "LinkStats", "ConsoleText", "RawFrame", "GsEvent", "Note",
            "VehicleStatus", "Actuators", "HandsetTelem", "ImuRaw", "Estimator", "TaskTiming", "TxModuleStats",
            "RxModuleStats", "CmdApplied", "CmdState", "RequestAck", "NetworkStatus", "Request", "LinkConfig",
            "Telecommand",
            "Calibration",
            "LegacyRow", "HomePosition",
    }  # fmt: skip


def test_directions_and_up_types():
    up = [p.name for p in REGISTRY.all() if p.direction is Direction.UP]
    # the uplink types: the command state (the engine) and whitelisted REQUESTs. Telecommand stays only so old logs
    # decode (retired, the wire profile never encodes it); RC_STATE is receive-only since the GS-control cutover
    assert up == ["CmdState", "Request", "Telecommand"]
    assert REGISTRY.get("RcState").direction is Direction.DOWN
    assert REGISTRY.get("LinkStats").direction is Direction.LOCAL


def test_field_specs_from_declaration():
    ptype = REGISTRY.get("Attitude")
    q = ptype.field("q")
    assert (q.kind, q.length, q.labels) == ("float_array", 4, ("w", "x", "y", "z"))
    assert ptype.field("tilt_acc").unit == "rad"
    assert REGISTRY.get("RcState").field("ch_us").length == 16
    assert REGISTRY.get("MissionState").field("t_mission").kind == "time_ns"
    assert REGISTRY.get("VehicleState").field("mode").enum.__name__ == "VehicleMode"
    assert Attitude.TYPE == "Attitude" and Attitude.VERSION == 1 and RcState.PTYPE.name == "RcState"


def test_every_field_is_optional_by_default():
    pkt = REGISTRY.get("VehicleState").cls(t=1, src="x")
    assert all(getattr(pkt, s.name) is None for s in REGISTRY.get("VehicleState").fields)


def test_duplicate_and_unknown():
    with pytest.raises(ValueError):
        REGISTRY.register(REGISTRY.get("Note"))
    with pytest.raises(KeyError):
        REGISTRY.get("Nope")


def test_decorator_with_private_registry():
    class Color(IntEnum):
        Red = 0

    reg = PacketRegistry()

    @packet("Demo", version=2, direction=Direction.DOWN, registry=reg, desc="d")
    class Demo(Packet):
        x: float | None = f(unit="m")
        c: Color = f()
        n: list[int] = f(length=3)

    assert [p.name for p in reg.all()] == ["Demo"]
    assert reg.get("Demo").field("n").kind == "int_array"
    assert "Demo" not in {p.name for p in REGISTRY.all()}


def test_unsupported_annotation_rejected():
    with pytest.raises(TypeError):

        @packet("Bad", version=1, direction=Direction.DOWN, registry=PacketRegistry())
        class Bad(Packet):
            x: dict = f()


def test_schema_is_json_serialisable_and_complete():
    schema = REGISTRY.schema()
    json.dumps(schema)
    att = next(t for t in schema["types"] if t["name"] == "Attitude")
    assert att["direction"] == "down" and att["history"] is True
    assert {"name", "kind", "length", "unit", "enum", "labels", "desc"} <= set(att["fields"][0])
    vs = next(t for t in schema["types"] if t["name"] == "VehicleState")
    assert vs["fields"][0]["enum"][:2] == ["Startup", "SensorInit"]
