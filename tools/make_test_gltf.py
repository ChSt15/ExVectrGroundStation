"""Write models/test-box.gltf: two boxes with known axes, used by the glTF loading test
(tests/integration/test_view3d.py).

glTF is Y-up, metres. Node "hull" is 0.10 (X) x 0.40 (Y, long) x 0.20 (Z). Node "flap" is 0.06 (X) x 0.12 (Y) x
0.004 (Z), with its hinge edge on the Y axis (X from 0 to 0.06). The node translations are deliberately non-zero:
the loader must ignore them for a `node` source (model_to_body places the model).
"""

import base64
import json
import struct
import sys
from pathlib import Path


def box(x0, x1, y0, y1, z0, z1):
    v = [(x, y, z) for x in (x0, x1) for y in (y0, y1) for z in (z0, z1)]
    # vertex index = 4*ix + 2*iy + iz
    quads = [(0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6), (0, 2, 6, 4), (1, 5, 7, 3)]
    idx = []
    for a, b, c, d in quads:
        idx += [a, b, c, a, c, d]
    return v, idx


def build() -> dict:
    hull_v, hull_i = box(-0.05, 0.05, -0.2, 0.2, -0.1, 0.1)
    flap_v, flap_i = box(0.0, 0.06, -0.06, 0.06, -0.002, 0.002)
    blob = b""
    views, accessors, meshes = [], [], []
    for verts, idxs in ((hull_v, hull_i), (flap_v, flap_i)):
        pos = b"".join(struct.pack("<3f", *p) for p in verts)
        ind = b"".join(struct.pack("<H", i) for i in idxs) + b"\x00\x00"
        ind = ind[: len(idxs) * 2 + (len(idxs) * 2) % 4]
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(pos), "target": 34962})
        blob += pos
        views.append({"buffer": 0, "byteOffset": len(blob), "byteLength": len(idxs) * 2, "target": 34963})
        blob += ind
        lo = [min(p[k] for p in verts) for k in range(3)]
        hi = [max(p[k] for p in verts) for k in range(3)]
        accessors.append(
            {
                "bufferView": len(views) - 2,
                "componentType": 5126,
                "count": len(verts),
                "type": "VEC3",
                "min": lo,
                "max": hi,
            }
        )
        accessors.append({"bufferView": len(views) - 1, "componentType": 5123, "count": len(idxs), "type": "SCALAR"})
        meshes.append({"primitives": [{"attributes": {"POSITION": len(accessors) - 2}, "indices": len(accessors) - 1}]})
    return {
        "asset": {"version": "2.0", "generator": "tools/make_test_gltf.py"},
        "scene": 0,
        "scenes": [{"nodes": [0, 1]}],
        "nodes": [
            {"name": "hull", "mesh": 0, "translation": [0.3, 0.1, 0.0]},
            {"name": "flap", "mesh": 1, "translation": [-0.2, 0.0, 0.5]},
        ],
        "meshes": meshes,
        "accessors": accessors,
        "bufferViews": views,
        "buffers": [
            {"byteLength": len(blob), "uri": "data:application/octet-stream;base64," + base64.b64encode(blob).decode()}
        ],
    }


if __name__ == "__main__":
    out = (
        Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent.parent / "models" / "test-box.gltf"
    )
    out.write_text(json.dumps(build(), indent=1) + "\n")
    print("wrote", out)
