#!/usr/bin/env python3
"""Kayseri yol agi uretimi.

Girdi:  OSM yollari (osmium geojsonseq) + Microsoft Road Detections (TSV satirlari: ULKE \t geojson)
Cikti:  r/<ad>.bin  : 0.1 derecelik karolar (gzip JSON), her karo {"e":[[sinif, lat5, lon5, dlat, dlon, ...], ...]}
        r/<m>.bin   : il geneli ana yollar katmani (ayni bicim)
        r/i.json    : surum ve ozet
Siniflar: 0 ana yol, 1 koy yolu, 2 yerlesim/servis, 3 OSM tarla yolu, 4 patika,
          5 uydu tespiti tarla yolu, 6 bosluk baglantisi, 9 otoyol
"""
import sys, os, json, gzip, time
from collections import defaultdict
import numpy as np
import shapely
from shapely import LineString, Point
from shapely.ops import substring
from pyproj import Transformer

T0 = time.time()
def log(*a):
    print(f"[{time.time()-T0:7.1f}s]", *a, flush=True)

LON0, LAT0, LON1, LAT1 = 34.70, 37.60, 37.15, 39.45
OSM_CLS = {"motorway": 9, "motorway_link": 9, "trunk": 0, "trunk_link": 0, "primary": 0, "primary_link": 0,
           "secondary": 0, "secondary_link": 0, "tertiary": 0, "tertiary_link": 0, "unclassified": 1, "road": 1,
           "residential": 2, "living_street": 2, "service": 2, "track": 3, "path": 4, "bridleway": 4}
ML, CON = 5, 6
NEAR_OSM = 12.0      # bu mesafede OSM yolu varsa uydu tespiti tekrar sayilir
MIN_PIECE = 25.0     # bundan kisa uydu parcalari atilir
GAP = 30.0           # kopuk uclar bu mesafedeki yola baglanir
SIMPLIFY = 1.5

fw = Transformer.from_crs("EPSG:4326", "EPSG:32636", always_xy=True)
bw = Transformer.from_crs("EPSG:32636", "EPSG:4326", always_xy=True)

def to_m(a):
    x, y = fw.transform(a[:, 0], a[:, 1]); return np.column_stack([x, y])

def to_ll(a):
    x, y = bw.transform(a[:, 0], a[:, 1]); return np.column_stack([x, y])

def quick_in(p):
    return LON0 - 0.05 <= p[0] <= LON1 + 0.05 and LAT0 - 0.05 <= p[1] <= LAT1 + 0.05

def inbox(a):
    return a[:, 0].max() >= LON0 and a[:, 0].min() <= LON1 and a[:, 1].max() >= LAT0 and a[:, 1].min() <= LAT1

def read_osm(path):
    lines, cls, bridge = [], [], []
    if not path or not os.path.exists(path):
        return lines, cls, bridge
    with open(path, "rb") as f:
        for raw in f:
            raw = raw.strip().lstrip(b"\x1e")
            if not raw:
                continue
            try:
                ft = json.loads(raw)
            except Exception:
                continue
            pr = ft.get("properties") or {}
            c = OSM_CLS.get(pr.get("highway"))
            if c is None:
                continue
            if pr.get("area") == "yes":
                continue
            if c >= 2 and pr.get("access") in ("no", "private"):
                continue
            g = ft.get("geometry") or {}
            if g.get("type") != "LineString":
                continue
            co = g.get("coordinates") or []
            if len(co) < 2:
                continue
            a = np.asarray(co, dtype=float)[:, :2]
            if not inbox(a):
                continue
            lines.append(a); cls.append(c)
            br = (pr.get("bridge") not in (None, "no")) or (pr.get("tunnel") not in (None, "no")) or (pr.get("layer") not in (None, "0"))
            bridge.append(bool(br))
    return lines, cls, bridge

def read_ml(path):
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            p = line.rstrip("\n").split("\t", 1)
            if len(p) < 2:
                continue
            s = p[1]
            try:
                obj = json.loads(s)
            except Exception:
                continue
            g = obj.get("geometry", obj) if isinstance(obj, dict) else None
            if not g:
                continue
            t, co = g.get("type"), g.get("coordinates")
            parts = [co] if t == "LineString" else (co if t == "MultiLineString" else [])
            for part in parts:
                if not part or len(part) < 2 or not quick_in(part[0]):
                    continue
                a = np.asarray(part, dtype=float)[:, :2]
                if inbox(a):
                    out.append(a)
    return out

def proj_all(arrs):
    if not arrs:
        return []
    lens = [len(a) for a in arrs]
    m = to_m(np.concatenate(arrs))
    out, i = [], 0
    for n in lens:
        out.append(m[i:i + n]); i += n
    return out

def ml_not_near_osm(ml_m, osm_geoms):
    """Uydu tespitlerinden OSM yollarina NEAR_OSM metreden yakin kisimlari cikarir."""
    if not ml_m:
        return []
    geoms = shapely.segmentize(np.array([LineString(a) for a in ml_m], dtype=object), 8.0)
    coords, idx = shapely.get_coordinates(geoms, return_index=True)
    same = idx[:-1] == idx[1:]
    seg_start = np.nonzero(same)[0]
    mids = (coords[seg_start] + coords[seg_start + 1]) / 2.0
    near = np.zeros(len(seg_start), dtype=bool)
    if len(osm_geoms):
        tree = shapely.STRtree(osm_geoms)
        q = tree.query_nearest(shapely.points(mids), max_distance=NEAR_OSM, return_distance=False)
        near[np.unique(q[0])] = True
    seg_line = idx[seg_start]
    pieces = []
    order = np.argsort(seg_line, kind="stable")
    seg_line_s, seg_start_s, near_s = seg_line[order], seg_start[order], near[order]
    bounds = np.nonzero(np.diff(seg_line_s))[0] + 1
    starts = np.concatenate([[0], bounds]); ends = np.concatenate([bounds, [len(seg_line_s)]])
    for s0, e0 in zip(starts, ends):
        keep = ~near_s[s0:e0]
        if not keep.any():
            continue
        ss = seg_start_s[s0:e0]
        k = np.concatenate([[False], keep, [False]]).astype(np.int8)
        d = np.diff(k)
        rs = np.nonzero(d == 1)[0]; re_ = np.nonzero(d == -1)[0]
        for a, b in zip(rs, re_):
            c0 = ss[a]; c1 = ss[b - 1] + 1
            pts = coords[c0:c1 + 1]
            if len(pts) < 2:
                continue
            ln = LineString(pts)
            if ln.length >= MIN_PIECE:
                pieces.append(shapely.simplify(ln, SIMPLIFY))
    return pieces

class Graph:
    def __init__(self):
        self.key2id = {}; self.nxy = []; self.edges = []; self.adj = defaultdict(set)
    def node(self, x, y):
        k = (round(x * 10), round(y * 10))
        i = self.key2id.get(k)
        if i is None:
            i = len(self.nxy); self.key2id[k] = i; self.nxy.append((x, y))
        return i
    def add(self, coords, cls):
        coords = np.asarray(coords, dtype=float)
        if len(coords) < 2:
            return None
        u = self.node(*coords[0]); v = self.node(*coords[-1])
        if u == v and LineString(coords).length < 1.0:
            return None
        coords = coords.copy(); coords[0] = self.nxy[u]; coords[-1] = self.nxy[v]
        e = {"u": u, "v": v, "c": cls, "xy": coords, "ok": True}
        self.edges.append(e); i = len(self.edges) - 1
        self.adj[u].add(i); self.adj[v].add(i)
        return i
    def remove(self, i):
        e = self.edges[i]
        if not e["ok"]:
            return
        e["ok"] = False; self.adj[e["u"]].discard(i); self.adj[e["v"]].discard(i)
    def degree(self, n):
        return sum(2 if (self.edges[i]["u"] == self.edges[i]["v"]) else 1 for i in self.adj[n])
    def live(self):
        return [i for i, e in enumerate(self.edges) if e["ok"]]

def split_edges(G, requests):
    """requests: {edge_index: [along_m, ...]} -> {(edge_index, along): node_id}"""
    out = {}
    for ei, alongs in requests.items():
        e = G.edges[ei]
        if not e["ok"]:
            continue
        ln = LineString(e["xy"]); L = ln.length
        cuts = sorted(set(a for a in alongs if 1.0 < a < L - 1.0))
        if not cuts:
            for a in alongs:
                out[(ei, a)] = e["u"] if a <= L / 2 else e["v"]
            continue
        G.remove(ei)
        prev = 0.0; prev_node = e["u"]; node_at = {}
        for a in cuts + [L]:
            part = substring(ln, prev, a)
            pc = np.asarray(part.coords)
            if a < L:
                nid = G.node(*pc[-1]); pc[-1] = G.nxy[nid]; node_at[a] = nid
            else:
                nid = e["v"]
            pc[0] = G.nxy[prev_node]
            if a < L or True:
                G.add(pc, e["c"])
            prev = a; prev_node = nid
        for a in alongs:
            if a in node_at:
                out[(ei, a)] = node_at[a]
            else:
                out[(ei, a)] = e["u"] if a <= L / 2 else e["v"]
    return out

def main(osm_path, ml_path, outdir):
    os.makedirs(os.path.join(outdir, "r"), exist_ok=True)
    stats = {}
    osm_ll, osm_cls, osm_br = read_osm(osm_path)
    log("OSM yol sayisi", len(osm_ll)); stats["osm_yol"] = len(osm_ll)
    ml_ll = read_ml(ml_path)
    log("uydu tespiti cizgi sayisi", len(ml_ll)); stats["uydu_cizgi"] = len(ml_ll)

    osm_m = proj_all(osm_ll); ml_m = proj_all(ml_ll)
    osm_geoms = np.array([LineString(a) for a in osm_m], dtype=object)
    stats["osm_km"] = round(float(shapely.length(osm_geoms).sum()) / 1000, 1) if len(osm_geoms) else 0
    stats["uydu_km_ham"] = round(sum(LineString(a).length for a in ml_m) / 1000, 1) if ml_m else 0
    pieces = ml_not_near_osm(ml_m, osm_geoms)
    stats["uydu_parca"] = len(pieces); stats["uydu_km_yeni"] = round(sum(p.length for p in pieces) / 1000, 1)
    log("OSM'de olmayan uydu parcasi", len(pieces), "km", stats["uydu_km_yeni"])

    br = np.array(osm_br, dtype=bool) if len(osm_br) else np.zeros(0, dtype=bool)
    cls_arr = np.array(osm_cls, dtype=np.int16)
    flat_geoms = list(osm_geoms[~br]) + pieces
    flat_cls = np.concatenate([cls_arr[~br], np.full(len(pieces), ML, dtype=np.int16)])
    log("dugumleniyor", len(flat_geoms))
    noded = shapely.node(shapely.multilinestrings(flat_geoms)) if flat_geoms else None
    parts = shapely.get_parts(noded) if noded is not None else np.array([], dtype=object)
    log("parca", len(parts))
    tree = shapely.STRtree(np.array(flat_geoms, dtype=object))
    mids = shapely.line_interpolate_point(parts, 0.5, normalized=True)
    q = tree.query_nearest(mids, return_distance=False, all_matches=True)
    pcls = np.full(len(parts), 99, dtype=np.int16)
    np.minimum.at(pcls, q[0], flat_cls[q[1]])

    G = Graph()
    seen = set()
    for g, c in zip(parts, pcls):
        co = np.asarray(g.coords)
        a = (round(co[0][0] * 10), round(co[0][1] * 10)); b = (round(co[-1][0] * 10), round(co[-1][1] * 10))
        k = (min(a, b), max(a, b), round(g.length))
        if k in seen:
            continue
        seen.add(k); G.add(co, int(c))
    log("kenar", len(G.live()), "dugum", len(G.nxy))

    # kopru/tunel yollari: yalniz uclarindan baglanir
    if br.any():
        bgeoms = [osm_m[i] for i in np.nonzero(br)[0]]
        bcls = [osm_cls[i] for i in np.nonzero(br)[0]]
        live = G.live(); etree = shapely.STRtree(np.array([LineString(G.edges[i]["xy"]) for i in live], dtype=object))
        req = defaultdict(list); ends = []
        for co in bgeoms:
            for pt in (co[0], co[-1]):
                P = Point(pt)
                hit = etree.query(P, predicate="dwithin", distance=0.5)
                for h in hit:
                    ei = live[h]; a = LineString(G.edges[ei]["xy"]).project(P)
                    req[ei].append(a); ends.append((ei, a))
        split_edges(G, req)
        for co, c in zip(bgeoms, bcls):
            G.add(co, c)
        log("kopru/tunel", len(bgeoms))

    # bosluk baglantilari
    live = G.live()
    elines = np.array([LineString(G.edges[i]["xy"]) for i in live], dtype=object)
    etree = shapely.STRtree(elines)
    dangling = [n for n in range(len(G.nxy)) if G.degree(n) == 1]
    req = defaultdict(list); conns = []
    for n in dangling:
        inc = next(iter(G.adj[n])); e = G.edges[inc]
        if e["c"] == 9:
            continue
        other = e["v"] if e["u"] == n else e["u"]
        banned = set(G.adj[n]) | set(G.adj[other])
        P = Point(G.nxy[n])
        hits = etree.query(P, predicate="dwithin", distance=GAP)
        best = None
        for h in hits:
            ei = live[h]
            if ei in banned or not G.edges[ei]["ok"] or G.edges[ei]["c"] == 9:
                continue
            d = elines[h].distance(P)
            if best is None or d < best[0]:
                best = (d, ei, h)
        if best is None:
            continue
        d, ei, h = best
        a = elines[h].project(P)
        req[ei].append(a); conns.append((n, ei, a, d))
    nodes_at = split_edges(G, req)
    nc = 0
    for n, ei, a, d in conns:
        tgt = nodes_at.get((ei, a))
        if tgt is None or tgt == n:
            continue
        if d < 0.5:
            continue
        G.add(np.array([G.nxy[n], G.nxy[tgt]]), CON); nc += 1
    stats["baglanti"] = nc
    log("bosluk baglantisi", nc)

    # kucuk kopuk parcalari at
    parent = list(range(len(G.nxy)))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]; x = parent[x]
        return x
    for i in G.live():
        e = G.edges[i]; a, b = find(e["u"]), find(e["v"])
        if a != b:
            parent[a] = b
    comp_len = defaultdict(float); comp_osm = defaultdict(bool)
    for i in G.live():
        e = G.edges[i]; r = find(e["u"]); comp_len[r] += LineString(e["xy"]).length
        if e["c"] not in (ML, CON):
            comp_osm[r] = True
    dropped = 0
    for i in G.live():
        e = G.edges[i]; r = find(e["u"])
        if comp_len[r] < 50 or (not comp_osm[r] and comp_len[r] < 200):
            G.remove(i); dropped += 1
    stats["atilan_kucuk"] = dropped

    # ayni sinifli ardisik kenarlari birlestir
    merged = 0
    changed = True
    while changed:
        changed = False
        for n in range(len(G.nxy)):
            inc = list(G.adj[n])
            if len(inc) != 2:
                continue
            i1, i2 = inc
            e1, e2 = G.edges[i1], G.edges[i2]
            if i1 == i2 or e1["c"] != e2["c"] or not e1["ok"] or not e2["ok"]:
                continue
            if e1["u"] == e1["v"] or e2["u"] == e2["v"]:
                continue
            a = e1["xy"] if e1["v"] == n else e1["xy"][::-1]
            b = e2["xy"] if e2["u"] == n else e2["xy"][::-1]
            s = a[0]; t = b[-1]
            su = e1["u"] if e1["v"] == n else e1["v"]; tv = e2["v"] if e2["u"] == n else e2["u"]
            if su == tv:
                continue
            G.remove(i1); G.remove(i2)
            G.add(np.vstack([a, b[1:]]), e1["c"]); merged += 1; changed = True
    log("birlestirme", merged)

    # cikti
    live = G.live()
    nll = to_ll(np.array(G.nxy)) if G.nxy else np.zeros((0, 2))
    tiles = defaultdict(list); major = []
    km = defaultdict(float)
    for i in live:
        e = G.edges[i]
        xy = np.asarray(shapely.simplify(LineString(e["xy"]), SIMPLIFY).coords)
        ll = to_ll(xy)
        ll[0] = nll[e["u"]]; ll[-1] = nll[e["v"]]
        q5 = np.round(ll * 1e5).astype(np.int64)  # lon5, lat5
        keep = np.ones(len(q5), dtype=bool); keep[1:] = np.any(q5[1:] != q5[:-1], axis=1)
        q5 = q5[keep]
        if len(q5) < 2:
            continue
        lat5 = q5[:, 1]; lon5 = q5[:, 0]
        rec = [int(e["c"]), int(lat5[0]), int(lon5[0])]
        for j in range(1, len(q5)):
            rec.append(int(lat5[j] - lat5[j - 1])); rec.append(int(lon5[j] - lon5[j - 1]))
        mid = q5[len(q5) // 2]
        key = f"{int(np.floor(mid[1] / 1e4))}:{int(np.floor(mid[0] / 1e4))}"
        tiles[key].append(rec)
        km[int(e["c"])] += LineString(xy).length / 1000
        if e["c"] in (0, 1, 9):
            major.append(rec)

    def fnv(s):
        h = 0x811c9dc5
        for b in s.encode():
            h ^= b; h = (h * 0x01000193) & 0xffffffff
        return f"{h:08x}"

    def write(name, recs):
        data = json.dumps({"e": recs}, separators=(",", ":")).encode()
        z = gzip.compress(data, 9)
        with open(os.path.join(outdir, "r", name + ".bin"), "wb") as f:
            f.write(z)
        return len(z)

    total = 0
    for key, recs in tiles.items():
        total += write(fnv("r" + key), recs)
    total += write(fnv("rm"), major)
    stats.update({"karo": len(tiles), "ana_kenar": len(major), "kenar": len(live), "mb": round(total / 1e6, 1),
                  "km_sinif": {str(k): round(v, 1) for k, v in sorted(km.items())}})
    with open(os.path.join(outdir, "r", "i.json"), "w") as f:
        json.dump({"v": time.strftime("%Y%m%d%H%M"), "t": len(tiles)}, f)
    with open(os.path.join(outdir, "s.json"), "w") as f:
        json.dump(stats, f, ensure_ascii=False, indent=1)
    log("bitti", json.dumps(stats, ensure_ascii=False))

if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None, sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else "out")
