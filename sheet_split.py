"""把一张角色三视图总览图切成正面 / 侧面 / 背面三张参考图。

不依赖 AI 视觉模型：先找上方角色排与下方细节排之间的横向空白，
再在上方角色排里按纵向空白切成三栏。适合常见的“一排三个角色”设定图。
"""

from __future__ import annotations

from io import BytesIO


class SheetSplitError(ValueError):
    pass


def split_character_sheet(raw: bytes) -> dict[str, bytes]:
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover
        raise SheetSplitError("缺少 Pillow，无法自动切分三视图") from exc

    try:
        img = Image.open(BytesIO(raw))
        img = ImageOps.exif_transpose(img).convert("RGB")
    except Exception as exc:  # noqa: BLE001
        raise SheetSplitError("无法读取这张图片") from exc
    if img.width < 120 or img.height < 120:
        raise SheetSplitError("图片太小，至少 120×120")

    small = img.copy()
    small.thumbnail((1000, 1000))
    w, h = small.size
    px = small.load()
    corners = [px[0, 0], px[w - 1, 0], px[0, h - 1], px[w - 1, h - 1]]
    bg = tuple(sum(c[i] for c in corners) // len(corners) for i in range(3))

    rows = [0] * h
    cols = [0] * w
    for y in range(h):
        for x in range(w):
            p = px[x, y]
            if max(
                abs(p[0] - bg[0]), abs(p[1] - bg[1]), abs(p[2] - bg[2])
            ) > 28:
                rows[y] += 1
                cols[x] += 1

    row_th = max(2, w // 300)
    ys = [y for y, n in enumerate(rows) if n >= row_th]
    if not ys:
        raise SheetSplitError("没有识别到画面内容")
    y0, y1 = ys[0], ys[-1]
    empty_th = max(2, w // 250)
    min_gap = max(6, h // 90)

    cut = None
    y = max(y0 + 1, int(h * 0.34))
    while y < min(y1, int(h * 0.9)):
        if rows[y] < empty_th:
            start = y
            while y < h and rows[y] < empty_th:
                y += 1
            if y - start >= min_gap:
                cut = start
                break
        y += 1
    if cut is None:
        cut = y1 + 1

    top_h = max(1, cut - y0)
    col_th = max(3, top_h // 90)
    active = [x for x in range(w) if cols[x] >= col_th]
    if not active:
        raise SheetSplitError("没有识别到三个角色")
    groups: list[tuple[int, int]] = []
    for x in active:
        if groups and x - groups[-1][1] <= max(4, w // 150):
            groups[-1] = (groups[-1][0], x)
        else:
            groups.append((x, x))
    groups = [g for g in groups if g[1] - g[0] > w * 0.1]

    if len(groups) != 3:
        content_x0 = min(active)
        content_x1 = max(active)
        step = (content_x1 - content_x0 + 1) / 3
        groups = [
            (int(content_x0 + i * step), int(content_x0 + (i + 1) * step) - 1)
            for i in range(3)
        ]

    scale = img.width / w
    pad_x = max(1, int(w * 0.006))
    pad_y = max(1, int(h * 0.006))
    out: dict[str, bytes] = {}
    for slot, (gx0, gx1) in zip(("front", "side", "back"), groups):
        box = (
            max(0, int((gx0 - pad_x) * scale)),
            max(0, int((y0 - pad_y) * scale)),
            min(img.width, int((gx1 + 1 + pad_x) * scale)),
            min(img.height, int((cut + pad_y) * scale)),
        )
        crop = img.crop(box)
        if crop.width < 64 or crop.height < 64:
            raise SheetSplitError("切分出来的图片太小，请换一张更清晰的三视图")
        buf = BytesIO()
        crop.save(buf, "PNG", optimize=True)
        out[slot] = buf.getvalue()
    return out
