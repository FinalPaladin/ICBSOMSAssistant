"""Draw.io (.drawio/.xml): giải nén -> đồ thị mxGraph -> mô tả luồng nghiệp vụ bằng tiếng Việt."""
import base64
import html
import math
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET
import zlib
from collections import deque

from .base import Chunk

MAX_CHARS = 3000


def _clean(value: str | None) -> str:
    v = value or ""
    v = re.sub(r"%3[cC]mxGraphModel.*$", "", v, flags=re.S)
    v = re.sub(r"<br\s*/?>|</?(?:div|p|li|tr|td|th|h[1-6])\b[^>]*>", " ", v, flags=re.I)
    v = re.sub(r"<[^>]+>", "", v)
    v = html.unescape(v)
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", v)).strip()


def _style_str(style: str, key: str) -> str:
    m = re.search(rf"(?:^|;){key}=([^;]+)", style)
    return m.group(1).strip().lower() if m else ""


def _is_step_badge(c: dict) -> bool:
    """Vòng tròn nhỏ đánh số bước (1, 2, ..., 12) vẽ rời trên sơ đồ — không phải nội dung nghiệp vụ."""
    lb = c["label"].strip()
    if not lb:
        return False
    if re.fullmatch(r"\d{1,2}", lb):
        return True
    if re.fullmatch(r"[A-Za-z]", lb) and "ellipse" in c["style"] and c["w"] <= 35 and c["h"] <= 35:
        return True
    return False


def _model_of(diagram: ET.Element) -> ET.Element | None:
    g = diagram.find("mxGraphModel")
    if g is not None:
        return g
    data = (diagram.text or "").strip()
    if not data:
        return None
    raw = base64.b64decode(data)
    xml = zlib.decompress(raw, -15).decode("utf-8")
    return ET.fromstring(urllib.parse.unquote(xml))


def _cells(model: ET.Element) -> dict[str, dict]:
    parents = {c: p for p in model.iter() for c in p}
    cells: dict[str, dict] = {}
    for el in model.iter():
        if el.tag in ("object", "UserObject"):
            inner = el.find("mxCell")
            if inner is None:
                continue
            cid, label = el.get("id"), el.get("label") or el.get("value") or ""
            src = inner
        elif el.tag == "mxCell":
            if parents.get(el) is not None and parents[el].tag in ("object", "UserObject"):
                continue
            cid, label, src = el.get("id"), el.get("value") or "", el
        else:
            continue
        if not cid:
            continue
        geo = src.find("mxGeometry")
        x = float(geo.get("x", 0) or 0) if geo is not None else 0.0
        y = float(geo.get("y", 0) or 0) if geo is not None else 0.0
        w = float(geo.get("width", 0) or 0) if geo is not None else 0.0
        h = float(geo.get("height", 0) or 0) if geo is not None else 0.0
        pts: list[tuple[float, float]] = []
        if geo is not None:
            for arr in geo.findall("Array"):
                if arr.get("as") == "points":
                    for pt in arr.findall("mxPoint"):
                        pts.append((float(pt.get("x", 0) or 0), float(pt.get("y", 0) or 0)))
        cells[cid] = {
            "id": cid, "label": _clean(label), "style": (src.get("style") or "").lower(),
            "vertex": src.get("vertex") == "1", "edge": src.get("edge") == "1",
            "source": src.get("source"), "target": src.get("target"), "parent": src.get("parent"),
            "x": x, "y": y, "w": w, "h": h, "points": pts,
        }
    return cells


def _is_lane(c: dict) -> bool:
    s = c["style"]
    return c["vertex"] and ("swimlane" in s or "pool" in s or "shape=table" in s)


def _style_float(style: str, key: str) -> float | None:
    m = re.search(rf"(?:^|;){key}=([-+]?\d*\.?\d+)", style)
    return float(m.group(1)) if m else None


def _abs_origin(cid: str | None, cells: dict[str, dict]) -> tuple[float, float]:
    ax, ay, cur, guard = 0.0, 0.0, cid, 0
    while cur and cur in cells and guard < 12:
        c = cells[cur]
        if c["vertex"]:
            ax += c["x"]
            ay += c["y"]
        cur = c.get("parent")
        guard += 1
    return ax, ay


def _anchor(box: tuple[float, float, float, float], px: float | None, py: float | None,
            other: tuple[float, float, float, float]) -> tuple[float, float]:
    ax, ay, w, h = box
    if px is not None and py is not None:
        return ax + px * w, ay + py * h
    cx, cy = ax + w / 2, ay + h / 2
    ox, oy = other[0] + other[2] / 2, other[1] + other[3] / 2
    dx, dy = ox - cx, oy - cy
    if abs(dx) >= abs(dy):
        return (ax + w, cy) if dx >= 0 else (ax, cy)
    return (cx, ay + h) if dy >= 0 else (cx, ay)


def _side_dir(px: float | None, py: float | None, p: tuple[float, float], box: tuple[float, float, float, float]) -> str:
    if px in (0.0, 1.0) and py not in (0.0, 1.0):
        return "h"
    if py in (0.0, 1.0) and px not in (0.0, 1.0):
        return "v"
    ax, ay, w, h = box
    if abs(p[0] - ax) < 1 or abs(p[0] - (ax + w)) < 1:
        return "h"
    if abs(p[1] - ay) < 1 or abs(p[1] - (ay + h)) < 1:
        return "v"
    return ""


def _edge_polyline(edge: dict, sbox: tuple[float, float, float, float],
                   tbox: tuple[float, float, float, float],
                   parent_xy: tuple[float, float]) -> list[tuple[float, float]]:
    st = edge["style"]
    ex, ey = _style_float(st, "exitx"), _style_float(st, "exity")
    nx, ny = _style_float(st, "entryx"), _style_float(st, "entryy")
    p0 = _anchor(sbox, ex, ey, tbox)
    p1 = _anchor(tbox, nx, ny, sbox)
    sdir = _side_dir(ex, ey, p0, sbox)
    tdir = _side_dir(nx, ny, p1, tbox)

    wps = [(parent_xy[0] + x, parent_xy[1] + y) for x, y in edge["points"]]
    if wps:
        poly = [p0]
        w0 = wps[0]
        if abs(p0[0] - w0[0]) > 1 and abs(p0[1] - w0[1]) > 1:
            poly.append((w0[0], p0[1]) if sdir == "h" else (p0[0], w0[1]))
        poly.extend(wps)
        wl = wps[-1]
        if abs(wl[0] - p1[0]) > 1 and abs(wl[1] - p1[1]) > 1:
            poly.append((wl[0], p1[1]) if tdir == "h" else (p1[0], wl[1]))
        poly.append(p1)
        return poly

    if abs(p0[0] - p1[0]) <= 1 or abs(p0[1] - p1[1]) <= 1:
        return [p0, p1]
    if sdir == "v" and tdir == "h":
        return [p0, (p0[0], p1[1]), p1]
    if sdir == "h" and tdir == "v":
        return [p0, (p1[0], p0[1]), p1]
    if sdir == "h" and tdir == "h":
        mx = (p0[0] + p1[0]) / 2
        return [p0, (mx, p0[1]), (mx, p1[1]), p1]
    if sdir == "v" and tdir == "v":
        my = (p0[1] + p1[1]) / 2
        return [p0, (p0[0], my), (p1[0], my), p1]
    return [p0, (p0[0], p1[1]), p1]


def _pt_seg_dist(px: float, py: float, a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx, dy = b[0] - a[0], b[1] - a[1]
    l2 = dx * dx + dy * dy
    if l2 <= 1e-6:
        return math.hypot(px - a[0], py - a[1]), 0.0
    t = max(0.0, min(1.0, ((px - a[0]) * dx + (py - a[1]) * dy) / l2))
    return math.hypot(px - (a[0] + t * dx), py - (a[1] + t * dy)), t * math.sqrt(l2)


def _box_poly_dist(box: tuple[float, float, float, float],
                   poly: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Trả về (khoảng cách gần nhất từ hộp nhãn tới cạnh, khoảng cách từ tâm nhãn, vị trí dọc cạnh)."""
    ax, ay, w, h = box
    cx, cy = ax + w / 2, ay + h / 2
    sample_pts = [(cx, cy), (ax, cy), (ax + w, cy), (cx, ay), (cx, ay + h)]
    best_r, best_c, best_along = 1e9, 1e9, 0.0
    cum = 0.0
    for a, b in zip(poly, poly[1:]):
        cd, ca = _pt_seg_dist(cx, cy, a, b)
        rd = min(_pt_seg_dist(sx, sy, a, b)[0] for sx, sy in sample_pts)
        # Kiểm tra trường hợp đoạn thẳng đi xuyên ngang/dọc qua hộp nhãn
        if abs(a[1] - b[1]) <= 1 and ay <= a[1] <= ay + h and max(min(a[0], b[0]), ax) <= min(max(a[0], b[0]), ax + w):
            rd = 0.0
        elif abs(a[0] - b[0]) <= 1 and ax <= a[0] <= ax + w and max(min(a[1], b[1]), ay) <= min(max(a[1], b[1]), ay + h):
            rd = 0.0
        if (rd, cd) < (best_r, best_c):
            best_r, best_c, best_along = rd, cd, cum + ca
        cum += math.hypot(b[0] - a[0], b[1] - a[1])
    return best_r, best_c, best_along


def _describe(page: str, title: str, cells: dict[str, dict]) -> list[Chunk]:
    lanes = {i: c for i, c in cells.items() if _is_lane(c)}

    def actor_of(c: dict) -> str:
        p, guard = c.get("parent"), 0
        while p and guard < 10:
            if p in lanes and lanes[p]["label"]:
                return lanes[p]["label"]
            p = cells.get(p, {}).get("parent")
            guard += 1
        return ""

    edge_labels: dict[str, list[str]] = {}
    for c in cells.values():
        if c["vertex"] and c["label"] and c["parent"] in cells and cells[c["parent"]]["edge"]:
            edge_labels.setdefault(c["parent"], []).append(c["label"])

    nodes = {i: c for i, c in cells.items()
             if c["vertex"] and i not in lanes and c["label"]
             and not (c["parent"] in cells and cells[c["parent"]]["edge"])}

    abs_boxes: dict[str, tuple[float, float, float, float]] = {}
    for nid, n in nodes.items():
        ax, ay = _abs_origin(nid, cells)
        abs_boxes[nid] = (ax, ay, n["w"], n["h"])

    edges: list[dict] = []
    connected_set: set[str] = set()
    dangling = 0
    for c in cells.values():
        if not c["edge"]:
            continue
        s, t = c["source"], c["target"]
        if s in nodes and t in nodes:
            edges.append(c)
            connected_set.add(s)
            connected_set.add(t)
        else:
            dangling += 1

    # Gắn các text box rời (không có mũi tên nối) nằm sát mũi tên thành nhãn của mũi tên đó
    edge_polys = {
        e["id"]: _edge_polyline(e, abs_boxes[e["source"]], abs_boxes[e["target"]], _abs_origin(e.get("parent"), cells))
        for e in edges
    }
    floating_attached: dict[str, list[tuple[float, str]]] = {}
    matched_isolated: set[str] = set()
    for nid, n in nodes.items():
        if nid in connected_set:
            continue
        if _is_step_badge(n):
            matched_isolated.add(nid)
            continue
        box = abs_boxes[nid]
        best_eid, best_score, best_along = None, (1e9, 1e9), 0.0
        for eid, poly in edge_polys.items():
            rd, cd, along = _box_poly_dist(box, poly)
            if rd <= 32.0 and (rd, cd) < best_score:
                best_eid, best_score, best_along = eid, (rd, cd), along
        if best_eid is not None:
            floating_attached.setdefault(best_eid, []).append((best_along, n["label"]))
            matched_isolated.add(nid)

    # Nhận diện Legend màu sắc (hộp màu có tên hệ thống ngắn: MKP, OMS, AX, Geodis WMS...)
    color_legend: dict[str, str] = {}
    legend_ids: set[str] = set()
    for nid, n in nodes.items():
        if nid in connected_set or nid in matched_isolated:
            continue
        fc = _style_str(n["style"], "fillcolor")
        lb = n["label"].strip()
        if (
            fc and fc not in ("none", "default", "#ffffff", "white")
            and 2 <= len(lb) <= 22
            and not any(ch in lb for ch in (".", ":", ";", "=", "\n"))
            and n["w"] <= 220 and n["h"] <= 100
        ):
            color_legend.setdefault(fc, lb)
            legend_ids.add(nid)

    def actor_with_system(c: dict) -> str:
        actor = actor_of(c)
        fc = _style_str(c["style"], "fillcolor")
        sys_name = color_legend.get(fc, "")
        if not sys_name:
            return actor
        if not actor:
            return sys_name
        if sys_name.lower() in actor.lower() or actor.lower() in sys_name.lower():
            return actor
        if any(w in actor.lower() for w in sys_name.lower().split() if len(w) >= 4):
            return actor
        return f"{actor} ({sys_name})"

    out: dict[str, list[tuple[str, list[str]]]] = {i: [] for i in nodes}
    indeg: dict[str, int] = {i: 0 for i in nodes}
    undir: dict[str, set[str]] = {i: set() for i in nodes}
    for e in edges:
        s, t = e["source"], e["target"]
        parts_lb: list[str] = []
        if e["label"] and not re.fullmatch(r"\d{1,2}", e["label"].strip()):
            parts_lb.append(e["label"])
        for elb in edge_labels.get(e["id"], []):
            if not re.fullmatch(r"\d{1,2}", elb.strip()):
                parts_lb.append(elb)
        if e["id"] in floating_attached:
            for _, flb in sorted(floating_attached[e["id"]], key=lambda x: x[0]):
                if flb not in parts_lb and not re.fullmatch(r"\d{1,2}", flb.strip()):
                    parts_lb.append(flb)
        out[s].append((t, parts_lb))
        indeg[t] += 1
        undir[s].add(t)
        undir[t].add(s)

    def _is_api_label(lb: str) -> bool:
        s = lb.strip()
        return s.startswith("/") or s.lower().startswith(("http://", "https://"))

    def _fmt_edge_meta(labels: list[str], is_decision: bool) -> tuple[str, str]:
        """Tách nhãn điều kiện (OK/FAIL/...) khỏi đường dẫn API và ghi chú bổ sung."""
        apis = [x for x in labels if _is_api_label(x)]
        others = [x for x in labels if not _is_api_label(x)]
        cond = ""
        notes = others
        if is_decision and others:
            cond = others[0]
            notes = others[1:]
        suffix_parts: list[str] = []
        if apis:
            suffix_parts.append("gọi API: " + ", ".join(apis))
        if notes:
            suffix_parts.append("; ".join(notes))
        suffix = (" — " + "; ".join(suffix_parts)) if suffix_parts else ""
        return cond, suffix

    def pos(i):
        ax, ay, _, _ = abs_boxes[i]
        return (ay, ax)

    connected = [i for i in nodes if i in connected_set]
    free_isolated = sorted(
        (i for i in nodes if i not in connected_set and i not in matched_isolated and i not in legend_ids),
        key=pos,
    )

    # Tách từng sơ đồ con (connected component) để không cắt ngang giữa chừng một luồng nghiệp vụ
    comp_seen: set[str] = set()
    components: list[list[str]] = []
    for nid in sorted(connected, key=pos):
        if nid in comp_seen:
            continue
        comp: list[str] = []
        dq = deque([nid])
        comp_seen.add(nid)
        while dq:
            u = dq.popleft()
            comp.append(u)
            for v in undir[u]:
                if v not in comp_seen:
                    comp_seen.add(v)
                    dq.append(v)
        components.append(comp)
    components.sort(key=lambda cp: min(abs_boxes[i][1] for i in cp))

    comp_bounds = [
        (min(abs_boxes[i][1] for i in cp), max(abs_boxes[i][1] + abs_boxes[i][3] for i in cp))
        for cp in components
    ]

    # Gán tiêu đề sơ đồ con & ghi chú rời về đúng component gần nhất theo trục Y
    comp_titles: list[str] = [""] * len(components)
    comp_notes: list[list[str]] = [[] for _ in components]
    unassigned_notes: list[str] = []
    for nid in free_isolated:
        n = nodes[nid]
        _, ny, nw, nh = abs_boxes[nid]
        if not components:
            unassigned_notes.append(n["label"])
            continue
        # Kiểm tra xem có phải thanh tiêu đề nằm ngay phía trên một component không
        best_title_idx = None
        if nw >= 260 and nh <= 80 and len(n["label"]) <= 120:
            for idx, (ymin, _) in enumerate(comp_bounds):
                if -20 <= ymin - (ny + nh) <= 220 and not comp_titles[idx]:
                    best_title_idx = idx
                    break
        if best_title_idx is not None:
            comp_titles[best_title_idx] = n["label"]
            continue
        # Gắn ghi chú vào component bao quanh hoặc gần nhất theo trục Y
        best_idx = min(
            range(len(comp_bounds)),
            key=lambda idx: 0.0 if comp_bounds[idx][0] - 80 <= ny <= comp_bounds[idx][1] + 80
            else min(abs(ny - comp_bounds[idx][0]), abs(ny - comp_bounds[idx][1])),
        )
        comp_notes[best_idx].append(n["label"])

    is_default_page = bool(re.fullmatch(r"(?:page|trang)[-\s]*\d+", page.strip(), flags=re.I))
    page_label = title if is_default_page else f"{title} – {page}"

    chunks: list[Chunk] = []
    for cidx, comp in enumerate(components):
        comp_set = set(comp)
        order: list[str] = []
        seen: set[str] = set()

        def bfs(start: str):
            dq = deque([start])
            while dq:
                n = dq.popleft()
                if n in seen:
                    continue
                seen.add(n)
                order.append(n)
                for t, _ in out[n]:
                    if t in comp_set and t not in seen:
                        dq.append(t)

        for s in sorted((i for i in comp if indeg[i] == 0), key=pos):
            bfs(s)
        for s in sorted((i for i in comp if i not in seen), key=pos):
            bfs(s)

        lines: list[str] = []
        for n in order:
            c = nodes[n]
            actor = actor_with_system(c)
            subj = f"{actor} thực hiện " if actor else ""

            def target_desc(tid: str) -> str:
                ta = actor_with_system(nodes[tid])
                tlabel = nodes[tid]["label"]
                return f"{tlabel} (trên {ta})" if ta and ta != actor else tlabel

            s = c["style"]
            outs = out[n]
            if "rhombus" in s:
                br_items = []
                for t, lbs in outs:
                    cond, suffix = _fmt_edge_meta(lbs, True)
                    if cond:
                        br_items.append(f"nếu {cond} thì chuyển sang {target_desc(t)}{suffix}")
                    else:
                        br_items.append(f"chuyển sang {target_desc(t)}{suffix}")
                br = "; ".join(br_items) or "không có nhánh tiếp theo"
                prefix = f"{actor} kiểm tra " if actor else "Kiểm tra "
                lines.append(f"- {prefix}{c['label']}: {br}.")
            elif not outs:
                lines.append(f"- Kết thúc: {subj}{c['label']}.")
            elif len(outs) == 1:
                t, lbs = outs[0]
                _, suffix = _fmt_edge_meta(lbs, False)
                kind = "Bắt đầu: " if indeg[n] == 0 and ("ellipse" in s or "terminator" in s) else ""
                lines.append(f"- {kind}{subj}{c['label']}, sau đó chuyển sang {target_desc(t)}{suffix}.")
            else:
                tg = "; ".join(target_desc(t) + _fmt_edge_meta(lbs, False)[1] for t, lbs in outs)
                lines.append(f"- {subj}{c['label']}, sau đó chuyển sang: {tg}.")

        sub_title = comp_titles[cidx]
        flow_label = f"{page_label} – {sub_title}" if sub_title else page_label
        actors = sorted({actor_with_system(nodes[n]) for n in order if actor_with_system(nodes[n])})
        head = f"Luồng: {flow_label}."
        if actors:
            head += " Các bên/hệ thống tham gia: " + ", ".join(actors) + "."
        if comp_notes[cidx]:
            lines.append("Ghi chú bổ sung: " + "; ".join(comp_notes[cidx]))
        if not lines:
            continue

        group: list[str] = []
        size = 0
        parts: list[list[str]] = []
        for ln in lines:
            if size + len(ln) > MAX_CHARS and group:
                parts.append(group)
                group, size = [], 0
            group.append(ln)
            size += len(ln)
        if group:
            parts.append(group)
        for k, g in enumerate(parts, 1):
            tag = f" (phần {k}/{len(parts)})" if len(parts) > 1 else ""
            text = (head + "\n" if k == 1 else f"Luồng {flow_label} (tiếp theo):\n") + "\n".join(g)
            chunks.append(Chunk(text=text, section=f"{flow_label}{tag}",
                                meta={"flow_name": flow_label, "page_name": page, "warnings": dangling}))

    if unassigned_notes:
        chunks.append(Chunk(
            text=f"{page_label}.\nGhi chú bổ sung: " + "; ".join(unassigned_notes),
            section=f"{page_label} (Ghi chú)",
            meta={"flow_name": page_label, "page_name": page, "warnings": dangling},
        ))
    return chunks


def parse_drawio(path: str, title: str) -> list[Chunk]:
    root = ET.parse(path).getroot()
    pages: list[tuple[str, ET.Element]] = []
    if root.tag == "mxfile":
        for i, d in enumerate(root.findall("diagram"), 1):
            model = _model_of(d)
            if model is not None:
                pages.append((d.get("name") or f"Trang {i}", model))
    elif root.tag == "mxGraphModel":
        pages.append((title, root))
    else:
        raise ValueError("File không phải định dạng draw.io (mxfile/mxGraphModel)")
    chunks: list[Chunk] = []
    for name, model in pages:
        chunks += _describe(_clean(name) or name, title, _cells(model))
    return chunks
