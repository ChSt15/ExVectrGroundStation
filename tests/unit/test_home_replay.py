"""Home position in the recording and in replay: the recorder keeps HomePosition and Gps (every packet), and a replay
of the log brings both back, so the GNSS position relative to home can be recomputed from them. The GUI does the
conversion (web/app/gnss/gnss_logic.js `gnssNwuFromHome`, unit tested in web/test/gnss_logic.test.js and in the
browser replay test); this file holds the same WGS-84 formula in Python as an independent reference and checks it
against the position the fake vehicle reported at the same time."""

import math

from exvectr_gs.core import Bus, History, StateStore
from exvectr_gs.packets import REGISTRY
from exvectr_gs.recording import LogLibrary, LogReader
from exvectr_gs.replay import Replayer
from tests.support_logs import T0_NS, make_log

S = 1_000_000_000
A, E2 = 6378137.0, 0.00669437999014


def nwu_from_home(gps, home):
    """Reference of gnssNwuFromHome: [N, W, U] metres, WGS-84 tangent plane at the home latitude."""
    s = math.sin(home.lat)
    w = math.sqrt(1 - E2 * s * s)
    m = A * (1 - E2) / w**3
    n_prime = A / w
    return [
        (gps.lat - home.lat) * m,
        -(gps.lon - home.lon) * n_prime * math.cos(home.lat),
        gps.alt - home.alt,
    ]


def test_conversion_reference_values():
    class P:
        pass

    home, gps = P(), P()
    home.lat, home.lon, home.alt = math.radians(51.5), math.radians(10.0), 100.0
    gps.lat, gps.lon, gps.alt = home.lat, home.lon, 100.0
    assert nwu_from_home(gps, home) == [0.0, -0.0, 0.0]
    gps.lat = home.lat + math.radians(1e-4)  # 1e-4 deg north: 11.1 m (meridian radius at 51.5 deg is 6.39e6 m)
    gps.alt = 112.5
    n, w, u = nwu_from_home(gps, home)
    assert abs(n - 11.15) < 0.05 and w == 0 and u == 12.5
    gps.lat, gps.lon = home.lat, home.lon + math.radians(1e-4)  # east is negative W: 6.9 m at 51.5 deg
    n, w, u = nwu_from_home(gps, home)
    assert n == 0 and abs(w + 6.93) < 0.05


async def test_recording_holds_home_and_gps_and_replay_recomputes_the_metres(tmp_path):
    path = make_log(tmp_path, "dropouts", 30.0)
    types = {p.TYPE for _, p in LogReader(path).iter_packets()}
    assert {"HomePosition", "Gps", "Position"} <= types  # the recorder keeps every packet

    bus, store = Bus(), StateStore(REGISTRY, [])
    rp = Replayer(LogLibrary(tmp_path), bus, store, History(REGISTRY))
    await rp.open(path.name)
    checked, moved = 0, 0.0
    for t_s in (6.0, 12.0, 20.0, 28.0):  # after the fake's first home packet
        rp.seek(T0_NS + int(t_s * S))
        latest = {e.packet.TYPE: e.packet for e in store.snapshot()}
        home, gps, pos = latest["HomePosition"], latest["Gps"], latest["Position"]
        assert home.latlon_valid and home.alt_valid and home.epoch == 1
        n, w, u = nwu_from_home(gps, home)
        # the fake derives its GNSS from its own NWU position on a spherical earth: agreement to the model, not exact
        assert abs(n - pos.pos[0]) < 0.5 + 0.005 * abs(pos.pos[0]), (t_s, n, pos.pos)
        assert abs(w - pos.pos[1]) < 0.5 + 0.005 * abs(pos.pos[1]), (t_s, w, pos.pos)
        assert abs(u - pos.pos[2]) < 0.5, (t_s, u, pos.pos)
        moved = max(moved, abs(pos.pos[0]), abs(pos.pos[1]), abs(pos.pos[2]))
        checked += 1
    assert checked == 4 and moved > 2.0  # the check is not trivially zero
