# BUILDOUT_PLAN.md — the from-scratch feature campaign (post-v4.9.2)

**Status:** BUILDING — Phases A (v4.10.0) through E (v4.14.0) shipped; next up: Phase F.
**Owner directive:** every feature on this list gets built, from scratch, with **no code bloat** —
every line purposeful; honest SKIP lists over speculative generality; smallest correct algorithm wins.
**Provenance:** synthesized from an 8-agent parallel research pass (per-track dossiers appended as
Appendices A–H). Same discipline that shipped the Tracer (OCR_PLAN) and minipdf (MINIPDF_PLAN):
research → staged build behind tests → prove → ship. Each phase lands green across
`python3.12 tests/run_all.py`, scrubbed, committed, pushed, docs current.

---

## 0. Ground rules (bind every phase)

1. **Offline, from scratch, zero new deps.** Pure numpy + stdlib/tk; fitz and pypdf only where they
   already are (and Phase J retires pypdf). No vendor/product/person names — formats only.
2. **No bloat.** Each phase ships the minimal correct core + an explicit SKIP list. Nothing
   speculative: if no caller needs it, it isn't built. Every removal/simplification candidate found
   while building gets taken (the v4.9.1 airtight-pass standard).
3. **Deterministic tests first-class.** Every engine gets a plain-python suite in `tests/`; GUI
   behavior goes into `test_gui_construct.py`; anything replacing a library builds behind a flag
   with the incumbent as oracle until parity is proven.
4. **Invariants untouched.** verify.py is never weakened; the note style is law; every stamped-page
   guarantee holds through every phase.

## 1. What's already delivered (context)

| Engine | Replaced / built | Version |
|---|---|---|
| the Tracer (OCR) | retired Tesseract | v4.4–4.7 |
| minipdf (PDF writer) | retired reportlab | v4.8 |
| dnd router + OLE backend | retired tkinterdnd2 | v4.9 |
| Heartwood, Squawk Box, Holler, the Weaver, the Loft, Pipewright, the Backcheck, Fieldstitch/fieldpro, the Selvage, extrude, harvest, align, pano, bim3d viewer, fx | from-scratch since birth | v1–v4.3 |

Remaining third-party runtime: **fitz (pymupdf), pypdf, numpy**. Phase J removes pypdf.
fitz (the renderer verify.py stands on) and numpy stay — documented floor, not up for rebuild.

## 2. The phase plan

Ordered by dependency, then risk (small proven wins fund the big campaigns). One phase = one round =
one version. Acceptance gates are per-phase; a phase does not start until the previous one shipped.

| Phase | Feature | Version | Depends on |
|---|---|---|---|
| **A** | minipdf raster image XObjects (JPEG passthrough + Flate RGB) — restores the stake-sheet plan thumbnail, enables Daybook photo sheets — **SHIPPED v4.10.0** | v4.10.0 | — |
| **B** | BIM z-buffer software rasterizer (numpy, PhotoImage blit) with fx-tiered fallback to the painter — **SHIPPED v4.11.0** (`rfi_stamper/raster.py`) | v4.11.0 | — |
| **C** | BIM interaction: 6-plane section box (real clipping), 3D picking, measure-in-3D (HD/VD/SD in the Fieldstitch frame) — **SHIPPED v4.12.0** | v4.12.0 | B |
| **D** | Clash-lite: capsule/box + capsule/capsule interference, clustered findings through the Backcheck format, viewer highlight — **SHIPPED v4.13.0** (`rfi_stamper/clash.py`) | v4.13.0 | C (picking/highlight) |
| **E** | Vector drawing diff (addendum redline): segment match + change clustering + minipdf redline overlay — **SHIPPED v4.14.0** (`rfi_stamper/drawdiff.py`, the Slipsheet) | v4.14.0 | A (overlay PDF) |
| **F** | CPM scheduler: forward/backward pass, float, critical path — drives the existing canvas Gantt | v4.15.0 | — |
| **G** | OCR correction-review GUI: the human gate feeding Corrections.promote + per-firm FontProfiles | v4.16.0 | — |
| **H** | Tracer P5: touching-glyph residual (language-prior DP, merge candidates), honest new eval bar | v4.17.0 | G (review data helps) |
| **I** | IFC-lite import: STEP parser subset → walls/slabs/columns in the world frame, coverage-honest | v4.18.0 | B (viewing) |
| **J** | The pypdf retirement: from-scratch PDF reader/merger behind a flag, pypdf as oracle, then flip — runtime becomes fitz + numpy only | **v5.0.0** | A–I stable |

## 3. Per-phase acceptance gates (summary — details in each Appendix)

Every phase: full suite green twice · banned-name scrub · no new deps · docs current
(HANDOFF round note, CLAUDE.md map/gotchas, README if user-facing) · version bump · commit + push.

- **A:** fitz round-trips the placed image at exact position/size; qpdf-clean; deterministic bytes;
  fieldpro thumbnail path re-enabled and construct-tested.
- **B:** golden-image hash tests for fixed camera/model; an intersecting-geometry case the painter
  sorts wrong renders correctly; interaction latency within budget at fx "full"; painter fallback
  intact at lower tiers.
- **C:** clip/pick/measure math unit-tested headlessly (exact vertices/distances); measure numbers
  equal fieldpro's delta math on the same points; construct-test for UI.
- **D:** hand-computed clash fixtures (exact penetration distances); zero findings on a clean model;
  clustered one-finding-per-clash reporting; Backcheck lane cites rules.
- **E:** synthetic revision fixtures with exact add/remove counts; the split-collinear-segment case
  produces ZERO false diffs; redline overlay verifies via fitz.
- **F:** textbook network fixture with exact ES/EF/LS/LF/TF per task; cycle detection errors honestly.
- **G:** construct-test; a correction round-trips through Corrections.promote into a FontProfile;
  nothing trains without the human gate.
- **H:** eval harness: clean 0.00% unchanged (hard), speckle ≤2% guard unchanged (hard), touching
  tier improves to the researched honest bar; no regression anywhere.
- **I:** hand-authored IFC fixtures with exact vertex asserts; coverage report contract (imported /
  skipped counts per entity type); never crashes on unknown entities.
- **J:** staged like MINIPDF_PLAN — parse-parity vs pypdf across every test PDF (page count, boxes,
  rotation, text via fitz), then the merge paths behind `PLOOM_PDF_READER`, then the 36-RFI blind
  corpus + full pixel-diff pipeline, then flip + retire from requirements/spec.

---

## Appendices — full per-track research dossiers

- **Appendix A** — minipdf raster image XObjects
- **Appendix B** — the numpy z-buffer rasterizer
- **Appendix C** — section box, picking, measure-in-3D
- **Appendix D** — clash-lite interference checks
- **Appendix E** — IFC-lite import (STEP subset)
- **Appendix F** — the from-scratch PDF reader/merger (pypdf retirement)
- **Appendix G** — vector drawing diff + CPM scheduler
- **Appendix H** — OCR correction-review GUI + Tracer P5

---


# Appendix A — minipdf raster image XObjects

## Track 1 — Raster Image XObjects in minipdf

### Why now, and what "done" means

`minipdf` deliberately shipped without raster support (MINIPDF_PLAN §6 open-question 7 was resolved "drop it"); `Canvas.drawImage` raises `NotImplementedError` (`rfi_stamper/minipdf/canvas.py:267-270`) and the fieldpro stake-sheet plan-thumbnail band prints an honest "(no plan thumbnail…)" placeholder (`rfi_stamper/fieldpro.py:1761-1772`). This track adds the *minimal* ISO 32000-1 image slice — one image dict shape, two encodings, one placement operator — and re-enables the thumbnail. The retired call shape (commit `993010e~1`, fieldpro ~L1793) was:

```python
pix = page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75))
img = ImageReader(io.BytesIO(pix.tobytes("png")))     # PNG bytes → retired reader
c.drawImage(img, ix, iy, width=iw, height=ih)
```

The new surface keeps `drawImage(img, x, y, width=, height=)` but takes the fitz Pixmap *directly* (raw samples → Flate) or JPEG bytes/path (passthrough) — so **no PNG parser is ever written**.

Every claim below was validated by a scratchpad prototype: the exact dict + `cm` math round-trips through fitz with correct quadrant colors/orientation and `qpdf --check` reports clean.

### Industry norms a professional implementation gets right

| Norm | Spec | What it means here |
|---|---|---|
| Image = stream XObject | ISO 32000-1 §8.9.5 | `/Type /XObject /Subtype /Image` stream object, referenced from page `/Resources /XObject` |
| Unit-square image space | §8.9.5.1 | The `Do` operator paints the image into the **1×1 unit square at the origin**; all sizing/positioning is CTM: `q  w 0 0 h  x y cm  /ImN Do  Q` |
| Row order | §8.9.5.2 | Sample row 0 is the **top** row of the displayed image. fitz `pix.samples` is also top-row-first — **no flip needed** (prototype-verified) |
| JPEG passthrough | §7.4.8 (`/DCTDecode`) | Professional writers never transcode a JPEG: the file bytes *are* the stream data; only Width/Height/components are read from the SOF marker |
| Raw + Flate | §7.4.4 (`/FlateDecode`) | Uncompressed RGB/gray samples, `zlib.compress` — the standard path for synthetic/rendered pixels |
| Dedup by content | universal practice | The same image drawn twice (or on two pages) must serialize as **one** object |
| Determinism | house policy (document.py) | No timestamps, fixed zlib level, insertion-ordered registries — identical input ⇒ identical bytes |

### The image dictionary (the only shape we emit)

```
N 0 obj
<< /Type /XObject /Subtype /Image
   /Width <int px> /Height <int px>
   /ColorSpace /DeviceRGB | /DeviceGray
   /BitsPerComponent 8
   /Filter /DCTDecode | /FlateDecode
   /Length <exact stream bytes> >>
stream\n<data>\nendstream
```

Same stream framing document.py already uses for content streams (`/Length` excludes the EOL after `stream` and before `endstream`; binary bytes are legal in the body — offsets are byte-counted so nothing else changes).

### Placement math (prototype-verified)

For an image placed with its **lower-left corner** at `(x, y)` and display size `w × h` points:

```
q
w 0 0 h x y cm      % scale unit square to w×h, translate to (x,y)
/Im1 Do
Q
```

`fmt_num` formats all six matrix numbers (it already guards bool/NaN/-0). The `q…Q` pair is mandatory — `cm` is not otherwise undoable.

Aspect-fit (the fieldpro band already computes this inline; keep it at the call site, don't add a helper):

```
s = min(box_w / img_w, box_h / img_h);  draw_w, draw_h = img_w*s, img_h*s
```

### JPEG SOF header reader (~30 lines, stdlib `struct` only)

```python
_SOF        = {0xC0, 0xC1, 0xC2}                    # baseline / ext-seq / progressive
_STANDALONE = {0x01} | set(range(0xD0, 0xD8))       # TEM, RSTn — no length word

def jpeg_info(data):                                # -> (width, height, ncomp)
    if data[:2] != b"\xff\xd8": raise ValueError("not a JPEG (no SOI)")
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF: raise ValueError("JPEG marker desync")
        while data[i] == 0xFF: i += 1               # fill bytes before the code
        m = data[i]; i += 1
        if m in _STANDALONE: continue
        if m == 0xD9: break                         # EOI with no SOF
        (seglen,) = struct.unpack(">H", data[i:i+2])
        if m in _SOF:
            prec, h, w, ncomp = struct.unpack(">BHHB", data[i+2:i+8])
            if prec != 8:          raise ValueError(f"unsupported precision {prec}")
            if ncomp not in (1,3): raise ValueError("CMYK/unknown JPEG — out of scope")
            return w, h, ncomp
        if m == 0xDA: break                         # scan data reached, no SOF
        i += seglen
    raise ValueError("no usable SOF (arithmetic/lossless JPEG?)")
```

`ncomp==3 → /DeviceRGB`, `ncomp==1 → /DeviceGray`. Accepting SOF2 (progressive) is safe — fitz/mupdf decodes it; anything else raises, and the fieldpro call site's existing `try/except` turns that into the honest fallback band.

### Raw-samples path (fitz pixmaps)

Accept any object duck-typed like a fitz Pixmap (`samples, width, height, n, alpha, stride`):

- **Require** `alpha == 0` (fieldpro renders `get_pixmap(...)` — alpha=False is the default) and `n in (1, 3)`; raise otherwise (honest refusal, no silent conversion).
- **Guard** `stride == width * n`; if padded, re-pack rows by slicing (mupdf pixmaps are unpadded in practice, so this is a one-line assert-or-repack).
- Stream data = `zlib.compress(samples, 9)` — a **fixed** level for reproducibility.

Size expectation: a letter sheet rendered at zoom 0.75 is 459×594 px = 818 KB raw RGB; a line drawing Flate-compresses to roughly 20–80 KB. A grayscale render (`fitz.csGRAY`) halves the raw size and is a legitimate option for the thumbnail. JPEG passthrough costs exactly the source file size.

### Wiring into the three files

**`minipdf/images.py` (new, ~70 lines)** — `jpeg_info()` plus one `Image` value class produced by a single classifier `make_image(src)`:
- `bytes`/`str` path starting `FF D8` → `Image(w, h, cs, b"/DCTDecode", raw_file_bytes)`
- pixmap duck-type → `Image(w, h, cs, b"/FlateDecode", zlib.compress(samples, 9))`
- everything else → `TypeError` with an honest message ("JPEG bytes/path or an alpha-free gray/RGB pixmap").
- `Image.key` = `sha256(filter + b"|%d|%d|" % (w,h) + cs + data).digest()` for dedup.

**`document.py` (+~25 lines)** — mirror the font registry exactly:
- `self._images: dict[bytes, tuple[str, Image]]` keyed by `Image.key`; `_use_image(img) -> "Im1"…` (insertion-ordered, deterministic).
- `to_bytes()`: allocate one object number per image **after** the font objects; extend the shared resources dict to `<< /Font <<…>> /XObject << /Im1 9 0 R … >> >>` (one resources dict shared by all pages, same as fonts today — legal, qpdf-clean, keeps the object count flat); serialize each image as the stream object above.

**`content.py` (+~8 lines)**:
```python
def draw_image(self, img, x, y, w, h) -> "Content":
    key = self._doc._use_image(img)
    return (self.save().concat(w, 0, 0, h, x, y)
                .raw(encoding.pdf_name(key) + b" Do").restore())
```

**`canvas.py` (replace the guard, ~12 lines)**:
```python
def drawImage(self, image, x, y, width=None, height=None, **_kw):
    img = images.make_image(image)
    w = img.width  if width  is None else width      # reportlab default:
    h = img.height if height is None else height     # 1 px = 1 pt
    if w <= 0 or h <= 0: raise ValueError("drawImage size must be positive")
    self._c.draw_image(img, x, y, w, h)
```
Route through `self._c` (NOT `self._content`) so a page pending after `showPage()` materializes lazily — the documented reportlab-semantics gotcha. Use `content.save()/restore()` inside `draw_image`, not `canvas.saveState()`, so the façade's font stack is untouched.

**`fieldpro.py _package_sheet_pdf` (net ~+12 lines)** — restore the retired band minus the PNG detour:
```python
pix = page.get_pixmap(matrix=fitz.Matrix(0.75, 0.75))   # alpha=False default
s = min(thumb_w / pw, thumb_h / ph); iw, ih = pw*s, ph*s
c.drawImage(pix, ix, iy, width=iw, height=ih)           # then border rect + pin dots
```
Keep the whole band inside the original `try/except` with the existing "(no plan thumbnail…)" fallback for raster/absent/refused plans — the fallback text stays, it just becomes the exception path again.

### Build recipe (ordered)

1. `minipdf/images.py`: `jpeg_info` + `Image` + `make_image` (pure functions, no writer knowledge).
2. `document.py`: `_use_image` registry; extend `to_bytes()` numbering + shared `/XObject` resources + image stream serialization.
3. `content.py`: `Content.draw_image`.
4. `canvas.py`: real `drawImage` (delete the `NotImplementedError` guard); export `images` in `minipdf/__init__.py`.
5. Tests in `tests/test_minipdf.py` (new entries in the existing `main()` list) — see acceptance.
6. Re-enable the fieldpro thumbnail band; extend `tests/test_fieldstitch_pro.py`'s stake-package check.
7. Docs: MINIPDF_PLAN §6 Q7 flips from "dropped" to "implemented (two-path minimal)"; HANDOFF round entry; patch version bump.

### Sharp pitfalls

- **Row order paranoia**: both PDF image space and fitz samples are top-row-first, so raw samples go in *untouched*. Anyone "helpfully" flipping rows ships upside-down thumbnails; the quadrant-color acceptance test is the guard.
- **`cm` leakage**: an unbalanced `q` around the image matrix corrupts everything drawn after it on the page. `draw_image` must own its `q…Q`.
- **JPEG fill bytes**: `FF FF … FF C0` padding before a marker code is legal; the parser must skip runs of `FF` or it desyncs on real files.
- **RSTn/TEM markers have no length word** — reading a length there misparses the rest of the file.
- **Don't hash the Flate bytes in golden tests**: `zlib.compress` output is deterministic per zlib build but may change across zlib versions. In-process double-build byte equality is a valid determinism check; cross-version regression tests must decode-and-compare pixels (same policy MINIPDF_PLAN already applies to fitz rasterization).
- **Alpha pixmaps**: `pix.n == 4` with `alpha == 1` is RGBA, not CMYK — refuse loudly; silently writing 4-component data as `/DeviceRGB` produces garbage that still "opens fine" in lenient viewers.
- **Stride padding**: writing padded rows shears the image diagonally. Assert `stride == width * n` (or repack).
- **Dedup must key on content, not object identity** — the stake sheet is regenerated per package; two identical renders must not double the file size.
- **The lazy-page gotcha**: `drawImage` immediately after `showPage()` must materialize the next page via the `_c` property, or the image lands on the already-committed page.
- **No `/Info`, ever**: image work touches `to_bytes()`; keep the deterministic content-hash `/ID` and zero-metadata posture intact (it is a policy invariant, not a style choice).

### Acceptance criteria (deterministic + offline)

All added to `tests/test_minipdf.py`'s `main()` list, plain-python style:

1. **SOF parser unit**: hand-built SOF0/SOF2 segments (bytes literals) parse to the right `(w, h, ncomp)`; padded-`FF` marker, RSTn skip, CMYK (`ncomp=4`) and non-JPEG inputs raise `ValueError`.
2. **Flate round-trip**: a 4×4 RGB pixmap with four distinct quadrant colors drawn at a known rect; fitz renders at 2×; assert each quadrant's sampled color AND that the red quadrant is **top-left** (row-order guard); pixels outside the rect are white (nothing else changed).
3. **DCT passthrough round-trip**: JPEG bytes produced offline by `fitz.Pixmap(...).tobytes("jpg")` (verified working on the pinned fitz 1.28); assert the embedded stream bytes are byte-identical to the source JPEG (no transcode) and the rendered center pixel matches the fill color within JPEG tolerance (±16/channel).
4. **Gray path**: 1-component pixmap emits `/DeviceGray`, renders correctly.
5. **Dedup**: the same pixmap drawn twice on one page and once on a second page ⇒ exactly one `/Subtype /Image` occurrence in the output bytes.
6. **Determinism**: building the identical document twice yields identical bytes (existing `test_deterministic` pattern); xref offsets still byte-exact with image objects present.
7. **Refusals**: alpha pixmap, `ncomp=4` JPEG, zero/negative draw size ⇒ typed exceptions.
8. **qpdf advisory**: the image-bearing file passes `qpdf --check` when qpdf is on PATH (existing skip-if-absent pattern; prototype already confirmed clean).
9. **fieldpro re-enable** (in `test_fieldstitch_pro.py`): a stake package built against a vector plan renders a sheet whose thumbnail band contains non-white pixels and does NOT contain the "(no plan thumbnail" string; against a missing plan the fallback string is present (the honest path still works).

### SKIP list — what NOT to build

- **PNG decoding** — the only historical PNG consumer was an encode-decode detour around the retired reader; fitz pixmaps go in raw. No PNG parser, no predictor support.
- **CMYK JPEG** — 4-component JPEGs commonly carry APP14-flagged *inverted* values; correct handling needs `/Decode` heuristics and is a classic negative-image bug farm. Refuse with a clear error.
- **Alpha / `/SMask`** — needs a second grayscale XObject and PDF 1.4 transparency semantics; no consumer needs it (thumbnails are opaque renders).
- **EXIF orientation** — thumbnails come from fitz renders, never cameras; a rotated phone JPEG will display as stored. Documented, not handled.
- **`/Interpolate`** — renderer-dependent smoothing; hurts pixel determinism for zero product value.
- **Inline images (`BI…EI`)**, image masks, `/Indexed`/ICC color, 16-bit samples, `/CCITTFaxDecode`/`/JBIG2Decode`/`/JPXDecode` — none are ever emitted by this app.
- **An `ImageReader` compatibility class** — the one call site is in-repo; change the call, don't resurrect the wrapper.
- **A generic aspect-fit helper on Canvas** — the fieldpro site already has the two-line `min()` math.

### LOC estimate

~150 runtime lines total (`images.py` ~70, `document.py` +25, `content.py` +8, `canvas.py` +12, `fieldpro.py` net +12, `__init__.py` +3) plus ~130 test lines. No new dependencies (stdlib `struct`/`zlib` only).

### Open questions

1. Thumbnail render color: keep RGB (layer-pin contrast against a color plan) or render `fitz.csGRAY` to halve the ~20–80 KB Flate payload? Recommend: keep RGB at zoom 0.75; the sheet is a one-page deliverable.
2. Accept progressive (SOF2) JPEG passthrough, or baseline-only for maximum ancient-viewer compatibility? Recommend: accept — fitz (the app's own ground-truth renderer) decodes it, and refusal already degrades honestly.
3. Should `make_image` also accept an `HxW`/`HxWx3` uint8 numpy array (pano/align/tracer all hold arrays)? It is ~4 extra lines; recommend deferring until a real caller exists (no speculative generality).
4. Version scheme: patch (v4.9.x) since no default behavior changes and the only visible delta is the thumbnail band returning?


# Appendix B — the numpy z-buffer rasterizer

## Track 2 — Numpy software z-buffer rasterizer for the BIM viewer

### 2.0 Ground truth: what exists today

Read: `/home/user/RFI-stamper/rfi_stamper/bim.py` (398 LOC), `/home/user/RFI-stamper/rfi_stamper/gui/bim3d.py` (1032 LOC).

| Existing piece | Fact the rasterizer must respect |
|---|---|
| `bim.Camera` + `_basis(cam)` | Orbit camera; `(eye, right, up, fwd)` world unit vectors. Walk mode reuses the same camera with `dist=2.0` and the eye *inside* the model. |
| `bim.project_points(pts, cam, w, h)` | Returns `(sx, sy, depth)`, **y-down top-left screen**, `depth` = camera-space distance along `fwd`. Perspective: `f = (h/2)/tan(fov/2)`, `sx = w/2 + xc·f/d`; ortho: `k = (h/2)/(dist·tan(fov/2))`. It **clamps** `depth ≤ _EPS` to `_EPS` — fine for the painter, poison for a rasterizer (see pitfalls). |
| `bim.Face` | 3+ near-planar vertices in drawing order, `color` hex, `system`. Producers: `wall_faces` (vertical planar **quads**, one per wall per floor — open surfaces seen from both sides), `tube_faces` (pipe prism: `sides` side quads + 2 planar polygon caps, default 8). |
| `bim3d._render` shaded path | Painter's algorithm by **face-centroid** camera distance; culls any face with **any vertex** `depth ≤ 1e-6`; flat shade `lam = |n̂·L̂|` with `_LIGHT = normalize(0.45, 0.35, 0.82)`, bucketed `lamb = int(lam·12 + 0.5)`, color = `_mix(f.color, bg, 0.12 + 0.5·(1 − lamb/12))`; depth-cue `fade` = 6-bucket mix toward bg, max 0.45, skipped at fx quality `"off"`. |
| Overlays | Grid (drawn first), wireframe segments, sheet-plane polygons (clickable via canvas tags `sheet:j` + `find_overlapping`), pins, label chips, measure tape, walk HUD — all tk canvas items. |
| Adaptive LOD | `SLOW_FRAME = 0.028 s` / `FAST_FRAME = 0.012 s` measured per `_render`; `self._lod` decimates segments next frame; `_end_interaction()` restores full detail on release. This machinery is reused as-is for **resolution** scaling. |
| Blit precedent | `gui/pano.py::_to_photo`: `tk.PhotoImage(data=b"P6 %d %d 255 " + np.ascontiguousarray(arr).tobytes())`, re-rendered per drag via `after_idle` coalescing (`_schedule`/`_pending`). Same trick in `tab_compare.np_to_photo`, `viewer.py`. House gotcha: keep a Python reference to the PhotoImage. |
| fx tiers | `full` / `reduced` / `off` (`gui/fx.py`). Old hardware must stay usable at `reduced`/`off`. |
| Culling semantics | `hidden_systems` and the Horizon Slice cull faces by **centroid true z**; slope exaggeration distorts pipe z at render time only. These happen *before* geometry reaches the rasterizer — unchanged. |

The one correctness hole the z-buffer exists to fix: **interpenetrating faces**. Centroid-sorted painter's draws a whole polygon over or under another; a pipe prism passing through a wall quad, or two crossing walls, renders with the wrong half hidden. Per-pixel depth resolves it exactly. (Cyclic overlap of 3 faces — the textbook painter's failure — is also unfixable by any sort order.)

### 2.1 Industry norms — what a professional minimal software rasterizer gets right

* **Pixel-center sampling** at `(x + 0.5, y + 0.5)`; edge functions decide coverage (Pineda-style half-plane rasterization — the algorithm every GPU uses).
* **A gap-free, deterministic fill rule.** The exact industry answer is the top-left rule (a boundary pixel belongs to a triangle iff it lies on a *top* edge — exactly horizontal, going left in y-down CCW — or a *left* edge — going down). It guarantees each pixel on a shared edge is owned by exactly one triangle: no cracks, no double-draw.
* **Depth interpolated as 1/z** under perspective. Screen-space barycentrics are affine in *1/z*, not in *z*; lerping z itself misorders occlusion on large triangles at glancing angles. Ortho interpolates z directly. Since shading is flat (constant color per face), 1/z is the **only** attribute that needs interpolation — no perspective-correct attribute machinery.
* **Near-plane handling by clipping, not clamping.** Clamping behind-camera vertices smears triangles across the whole screen; rejecting whole triangles makes walls vanish as the walker enters a room. Clip each straddling triangle against `z = znear` in camera space (single-plane Sutherland–Hodgman: a triangle becomes 0, 1, or 2 triangles).
* **Backface culling only on closed, consistently wound meshes.** Wall quads here are open, unoriented, seen from both sides — culling them deletes walls. Correct call: two-sided fill (orient each projected triangle to positive area), cull only degenerate `|area| < ε`.
* **Flat Lambert** computed once per face in *world* space (`|n̂·L̂|`, two-sided), before projection.
* **Interaction norms** (CAD-viewer convention): 30–60 fps ideal; degrade-during-interaction + refine-on-idle is universal; ≥ 10 fps floor during orbit, full-quality refine within ~300 ms of release. This viewer already implements exactly this contract with `_lod` — the raster path swaps "decimate segments" for "halve resolution".
* **Silhouette/outline rendering** on CPU: image-space discontinuity detection over ID + depth buffers (the G-buffer edge method, Saito–Takahashi lineage). With a per-pixel face-ID buffer this is four `np.roll` comparisons — far cheaper and more robust than geometric silhouette extraction.

### 2.2 Architecture

New **GUI-free** module `rfi_stamper/raster.py` (pure numpy + stdlib, testable headless exactly like `bim.py`; zero tk imports). `bim3d.py` grows a raster branch inside the existing shaded path; the Face-building code (hidden systems, slice, slope exaggeration, `tube_faces`) is reused unchanged — the rasterizer replaces only the "project + centroid-sort + create_polygon" tail.

```
raster.py
    LIGHT                       # moved here from bim3d (single source)
    shade_rgb(color_rgb, normal, light, bg_rgb) -> (3,) uint8
        # THE painter-parity function: same 12-bucket lambert + mix
        # formula as bim3d today; bim3d's painter path imports it too.
    triangulate(faces) -> (tris (T,3,3) f64 world, fidx (T,) i32)
        # fan from vertex 0: quad -> 2 tris, octagon cap -> 6 tris
    render(faces, cam, w, h, bg_rgb, *, znear=0.05, depth_cue=True)
        -> Frame(rgb (h,w,3) u8, invz (h,w) f32, fid (h,w) i32)
    outline_mask(frame, rel=0.02) -> (h,w) bool     # fid/depth edges
```

`fid` (face index, −1 = background) rides along for free and powers both the silhouette overlay and future picking. Everything downstream of the blit — grid replacement aside (§2.3 step 8) — stays canvas: wireframe segments, sheet planes, pins, chips, measure, HUD. That preserves click hit-testing (`find_overlapping` + `sheet:` tags) and today's "lines draw over faces" look, byte-for-byte in behavior.

### 2.3 Build recipe (minimal-scope, ordered)

**1. Triangulate.** Fan from vertex 0. All current producers are planar (vertical wall quads, prism side quads, planar caps), so the fan is exact; `Face` already documents "assumed near-planar".

**2. Camera space.** Expose the basis publicly (one line in `bim.py`: `basis = _basis`) and compute, vectorized over all vertices: `v = P − eye`, `xc = v@right`, `yc = v@up`, `zc = v@fwd`. Do **not** call `project_points` — its `depth ≤ _EPS` clamp is the painter's crutch and corrupts clipping.

**3. Near-plane clip (perspective only).** Vector triage: triangles with all `zc ≥ znear` pass; all `zc < znear` drop; the few stragglers go through a small Python Sutherland–Hodgman against `z = znear` (`t = (znear − za)/(zb − za)`, lerp camera-space vertices; 1 or 2 output triangles). Ortho: no clip, but drop triangles fully behind an ortho near (`zc < −dist` is fine as "keep everything"; simply skip). `znear = 0.05` world units (0.6 in) serves both orbit and walk (`dist = 2.0`).

**4. Project + viewport.** The **same** formulas as `project_points` (perspective `f = (h/2)/tan(fov/2)`, `sx = w/2 + xc·f/zc`, `sy = h/2 − yc·f/zc`; ortho with `k`), so canvas overlays computed via `project_points` land on the identical pixels — a half-pixel mismatch makes measure ticks visibly miss raster corners.

**5. Vectorized triangle setup** (whole scene at once, no loop):

```
# screen verts a,b,c (T,2); orient positive: if area<0 swap b<->c (np.where)
area = (bx-ax)(cy-ay) - (by-ay)(cx-ax)          # 2x signed area, y-down
# edge coefficients, E(x,y) = A·x + B·y + C, one row per edge:
A0,B0,C0 = by-cy, cx-bx, bx*cy - cx*by           # edge bc (opposite a)
A1,B1,C1 = cy-ay, ax-cx, cx*ay - ax*cy           # edge ca
A2,B2,C2 = ay-by, bx-ax, ax*by - bx*ay           # edge ab
# 1/z is affine in screen space; its plane follows from the edge rows:
GA = (A0·iz_a + A1·iz_b + A2·iz_c) / area        # likewise GB, GC
# integer bbox clipped to [0,w-1]x[0,h-1]; drop area<eps or empty bbox
```

(Ortho: replace `iz` with `−zc` and the depth test stays "greater = nearer".)

**6. Per-triangle fill loop — vectorized inner fill, zero per-pixel Python.** Each edge function and 1/z is *affine*, so evaluation over the bbox is one outer-add of two 1-D arrays (this is the measured-fastest single-path formulation, §2.5):

```
px = (arange(x0, x1+1) + 0.5); py = (arange(y0, y1+1) + 0.5)
e0 = (A0*px + C0) + (B0*py)[:,None]              # ditto e1, e2
zi = (GA*px + GC) + (GB*py)[:,None]              # interpolated 1/z
sub = invz[y0:y1+1, x0:x1+1]                     # view (basic slicing!)
win = (e0 >= 0) & (e1 >= 0) & (e2 >= 0) & (zi > sub)
invz[sy,sx] = np.where(win, zi, sub)
img [sy,sx] = np.where(win[...,None], face_rgb[i], img[sy,sx])
fid [sy,sx] = np.where(win, fidx[i], fid[sy,sx])
```

Buffers: `img` prefilled with the theme canvas bg, `invz` zeros (0 = empty/far — every visible `1/z > 0`), `fid` −1. Fill rule: **inclusive `≥ 0` on all edges** with the deterministic tie rule "strict `>` depth test + fixed triangle order ⇒ first-drawn wins z-ties". Shared edges are double-covered, never cracked; within a quad the two tris are coplanar and same-colored, so the double-cover is invisible. (The full top-left rule is the industry-exact alternative; it buys nothing visible here and costs per-edge case analysis — documented, not built.)

**7. Shading.** Per face, once, before the loop: world normal from the first three vertices, `lam = |n̂·LIGHT|` (two-sided), 12-bucket mix toward bg — the exact `bim3d` formula, moved into `shade_rgb` and *imported back* by the painter path so toggling raster/painter never shifts a color. Depth-cue: same 6-bucket 0.45 fade per face (by mean camera depth), applied to `face_rgb` pre-fill; skip when fx quality is `"off"` (parity with today).

**8. Grid.** The canvas grid would be hidden under a full-canvas image. Convert it to geometry: each grid line becomes one thin ground-plane quad (width ≈ span/1500) fed through the same triangle path — ~80 extra tiny triangles, and the grid becomes correctly occluded by the building (an upgrade over today). Keep the canvas grid in painter mode.

**9. Blit.** `arr → tk.PhotoImage` via the P6 PPM trick (`pano._to_photo` pattern); `create_image(0, 0, anchor="nw")` **first**, then all canvas overlays above it. numpy row 0 = PPM top row = screen top: with bim's y-down projection there is **no flip anywhere** — if the building renders upside-down someone added one. Keep `self._photo` referenced (house gotcha).

**10. Resolution scaling + fallback (fx contract).** Reuse `_lod`: raster renders at scale `s ∈ {1.0, 0.5}` — during drag or after a `SLOW_FRAME` overrun, `s = 0.5` (¼ the pixels), upscaled by `np.repeat(arr, 2, 0).repeat(2, 1)` (pixel-double; `PhotoImage.zoom` is integer-only and slower). `_end_interaction()` restores `s = 1.0` — the refine-on-release norm, wired to hooks that already exist. Tier policy:

| fx quality | shaded path |
|---|---|
| `full` | raster z-buffer, `s=1.0` idle / `0.5` dragging, silhouette overlay on |
| `reduced` | painter's (today's path) — old-hardware promise; raster stays user-toggleable |
| `off` | painter's, no depth cue (unchanged today) |

Auto-fallback: if triangle count > ~6,000 (after slice/system culls) or the frame stays over `SLOW_FRAME` at `s = 0.5`, drop to painter for that model and say so in the hint label — honest degradation, no silent jank. Pipe LOD during drag: `tube_faces(sides=6)` and skip interior caps (~2.3× fewer prism triangles), restored on release.

**11. Silhouette overlay** (raster mode only): `edge = (fid != roll(fid, 1, ax)) | (fid != roll(fid, 1, ay))`, gated by a relative 1/z jump (`|Δinvz| > 0.02·invz`) so coplanar same-quad seams don't outline; darken those pixels 45% toward the theme fg before the blit. Crisp CAD-style occluding contours for the cost of four rolls and one where.

**12. Wireframe segments stay canvas lines on top** — exactly today's shaded-mode layering (lines over faces, no per-pixel line occlusion). Zero regression, zero new code; per-pixel hidden-line removal is on the skip list.

### 2.4 Sharp pitfalls

1. **`project_points` clamps `depth ≤ _EPS`** — reusing it feeds the rasterizer screen coordinates of behind-camera vertices that look valid; triangles smear across the frame. Clip in camera space first (step 3), project yourself (step 4).
2. **Painter's any-vertex-behind cull must not be copied** into the raster path — in walk mode it makes the room you're standing in vanish. That's the bug near-plane clipping fixes.
3. **Interpolate 1/z, never z**, under perspective (ortho: z is fine). The give-away failure: a large floor quad at a glancing angle popping through a small nearer object.
4. **No backface culling on wall quads** — they're open surfaces with no consistent winding; orient every projected triangle to positive area (two-sided) and cull only `|area| < ε`. `abs()` in the lambert already makes lighting two-sided.
5. **Fill-rule cracks vs double-draw**: strict `>` on all edges leaves 1-px background seams between adjacent triangles; inclusive `≥` double-covers. Choose inclusive + strict-`>` z + fixed draw order (deterministic tie-winner). Never mix `≥` and `>` across edges of the same pair.
6. **Pixel centers at +0.5** and the *same* viewport formula as `project_points`, or canvas overlays (measure ticks, pins) sit half a pixel off the raster geometry.
7. **y-down bookkeeping**: `sy` is already y-down; buffer index is `[row=int(sy), col=int(sx)]`. PPM writes rows top-first. Adding a "correcting" flip anywhere mirrors the model.
8. **In-place fancy indexing trap**: `invz[y0:y1+1, x0:x1+1]` is a view (basic slicing) so masked writes stick; `invz[mask_2d][...] = x` writes a **copy** and silently does nothing. The `np.where`-assignment form in step 6 sidesteps it.
9. **Degenerate triangles**: zero projected area (edge-on walls, zero-length grid stubs) divide by `area` — cull before computing `GA/GB/GC`, not after (NaN in the invz plane poisons the whole bbox).
10. **Don't build the "fully vectorized scatter" rasterizer** (flat candidate arrays for all triangles + `maximum.at`/lexsort resolve). Measured here: it wins ~20% on uniformly tiny triangles but degrades with bbox *area* — 2,000 triangles of ~120 px took **2.0 s/frame** vs 0.35 s for the loop, and one near-camera wall quad is a full-screen bbox. The per-triangle loop with affine evaluation is the right shape for this scene mix.
11. **tk specifics**: keep the PhotoImage reference; `np.ascontiguousarray` before `.tobytes()` (slices/`repeat` results can be non-contiguous); coalesce renders with the `after_idle` + `_pending` pattern from `pano.py` so drag events don't queue up.
12. **Determinism for goldens**: elementary float64 ops are bit-stable everywhere, but `sin/cos` of arbitrary angles come from libm and may differ by 1 ulp across platforms — enough to flip a boundary pixel. Golden scenes therefore use `yaw=0, pitch=0` (sin 0 / cos 0 exact ⇒ basis exact ⇒ bit-exact hash cross-platform); rotated-camera tests assert structure (specific pixels, counts), not hashes.
13. **Shade in float, quantize once** with the same rounding as `_mix` (`int(round(...))`) — a uint8 intermediate or a different rounding breaks the painter-parity test.
14. Slice/hidden-system culling on **true z** and slope exaggeration **before** `tube_faces` — already the house rule in `bim3d._render`; the raster branch consumes the same culled/distorted face list, never re-derives it.

### 2.5 Performance norms and measured budgets

Interactive target: ≥ 10 fps (≤ 100 ms/frame) during orbit at reduced quality, full-res refine ≤ ~300 ms on release, **zero idle cost** (render only on interaction — the existing contract). Measured on this dev container (slow single core; a desktop is typically 2–4× faster), 960×600, random overlapping triangles, the affine-evaluation loop of step 6:

| triangles | ~size | ms/frame | fps |
|---|---|---|---|
| 500 | 30 px | 30 | 34 |
| 1,000 | 30 px | 59 | 17 |
| 2,000 | 30 px | 116 | 8.6 |
| 2,000 | 120 px | 352 | 2.8 |
| 2,000 @ half-res | — | 90 | 11 |

Per-triangle overhead ≈ 55–60 µs here (numpy call overhead dominates small triangles; the naive 3-edge broadcast formulation measured ~85 µs — the affine trick is the difference). Real scene sizes: extruded wall model ≈ 2 tris/wall/floor (a 300-wall, 2-floor plan ≈ 1,200 tris); pipe prisms 28 tris/segment at `sides=8` with caps, 12 at drag LOD (`sides=6`, no caps) — 150 runs ≈ 4,200 idle / 1,800 dragging. Hence the working budgets: **comfortable ≤ ~2,500 triangles** (full-res idle frame ≤ ~150 ms worst-case here, ≤ ~60 ms on a desktop), **drag at half-res + pipe LOD keeps ~10 fps to ~4–5 k**, **auto-fallback to painter above ~6 k**. Add ~10–20 ms for PPM + PhotoImage construction of a 960×600 frame. These are honest slow-machine numbers; the fx `reduced` tier never runs the rasterizer at all.

### 2.6 Acceptance (deterministic, offline — `tests/test_raster.py`, plain-python like `tests/test_bim_faces.py`)

1. **Coverage unit**: one triangle with integer vertices on an 8×8 ortho frame covers a hand-computed pixel set exactly (pixel-center rule).
2. **Golden hash**: fixed scene (two wall quads + one pipe prism), ortho camera `yaw=0, pitch=0`, 320×240 → `hashlib.sha256(frame.rgb.tobytes())` equals a pinned constant; `PLOOM_REGOLD=1` rewrites the baseline. Camera choice makes this bit-exact cross-platform (pitfall 12).
3. **Painter-fails / z-buffer-wins**: two interpenetrating quads crossing in an X. Assert pixel P₁ (where A is nearer) shows A's color and P₂ shows B's; assert a 10-line centroid-sort painter emulation of the same scene gets one of them wrong — the documented reason this track exists.
4. **1/z correctness**: large glancing ground quad + small nearer post under perspective; assert the post's pixels survive (a z-lerp implementation fails this).
5. **Near-plane clip**: triangle straddling `znear` renders only its front portion — assert covered-pixel bbox is bounded and no smear row exists; a whole scene behind the camera renders pure background.
6. **No cracks**: a quad split into two triangles has zero background pixels strictly inside its projected interior.
7. **Two-sided walls**: the same wall quad renders from both yaw 0 and yaw 180 ortho views (no backface deletion).
8. **Painter parity**: `raster.shade_rgb` output equals `bim3d`'s `_mix`-formula color for sampled `(color, lam)` pairs (both import the one function).
9. **Determinism**: two consecutive renders byte-identical; two coplanar different-colored faces at a z-tie resolve to the documented first-drawn winner regardless of rerun.
10. **GUI construct (xvfb)**: extend `tests/test_bim.py` — toggle raster mode, render demo model, assert a PhotoImage exists on the canvas and sheet-chip click routing still fires (canvas image item doesn't swallow `find_overlapping`).
11. **Perf tripwire, non-flaky**: demo-scene render < 2 s (catches an accidental per-pixel Python loop, nothing tighter).

### 2.7 SKIP list — not building

Texture mapping, gouraud/phong (flat only — matches the drawing-set aesthetic), MSAA/FXAA (half-res-refine covers interaction; goldens want aliased determinism anyway), full frustum clipping (near plane only; bbox clamp handles the sides), the top-left fill rule (inclusive fill + deterministic z-tie documented instead), the vectorized-scatter rasterizer (measured pathological on large triangles), per-pixel hidden-line removal for wireframe segments (canvas lines on top, parity with today), backface culling as a perf feature (unoriented open quads; z-buffer already pays correctness), shadows/AO, render threads (tk is single-threaded; frame fits the budget), C extensions/numba (dependency policy), BVH/occlusion culling (≤ 6 k tris), picking via `fid` (buffer exists; wiring is future work), rasterizing sheet planes/pins/chips/measure/HUD (interactive canvas UI stays canvas).

### 2.8 LOC estimate

`raster.py` ≈ 240; `bim3d.py` integration (mode branch, scale/fallback, blit, grid-as-quads) ≈ 90; `bim.py` +2 (public `basis`); **≈ 330 shipped LOC**, plus ≈ 250 test LOC. Nothing removed; painter mode remains intact as the fallback and the `reduced`/`off` tier path.

### 2.9 Open questions

1. `reduced` tier: painter's (recommended — the old-hardware promise) or raster capped at half-res? Owner call.
2. Silhouette overlay default-on at `full`, or opt-in? It changes the viewer's look (not stamped output, so no proof-sheet sign-off required — but worth one screenshot approval).
3. Should walk mode force raster on (it benefits most from near-clipping) even when the user left Shaded off?
4. Grid-as-geometry makes the grid occludable (behavior change from "grid always behind everything"). Accept, or drop the grid in raster mode?
5. Golden baseline storage: pinned hex constants in the test (recommended, zero binary files) vs committed `.npy` baselines with tolerance compare.


# Appendix C — section box, picking, measure-in-3D

## Track 3 — BIM Viewer Interaction: 6-Plane Section Box, 3D Measure, Picking

### 0. Baseline — what the repo already has (build on it, don't duplicate it)

| Existing piece | Where | Reuse decision |
|---|---|---|
| Orbit camera `(yaw, pitch, dist, target, fov, ortho)` + `_basis()` → `(eye, right, up, fwd)` and `project_points()` (y-down screen, depth = distance along `fwd`) | `rfi_stamper/bim.py:33-79` | The pick ray is the **exact algebraic inverse** of this — derive it from `_basis`, never re-derive a second camera model |
| Horizon Slice: single z-cut by **midpoint/centroid culling** (documented as a deliberate cheap approximation) | `gui/bim3d.py` `_render` | The section box supersedes it with real clipping; recommend the slider becomes the box's z-max control (open question 1) |
| Measure mode: two-click, 12 px endpoint snap, dashed tape, `fmt_ftin` distance + ΔZ, true-vs-drawn point pairs (slope-exaggeration safe) | `gui/bim3d.py` `_measure_click` / `_snap_endpoint` / `_draw_measure` | Keep the whole interaction shell; upgrade the **pick** (vertex>edge>face) and the **label** (HD/VD/SD + ΔN/ΔE + azimuth) |
| Delta math single source of truth: `dN/dE/dZ`, `HD = hypot(dN,dE)`, `azimuth = atan2(dE,dN) % 360`, `cut_fill()` | `rfi_stamper/fieldpro.py:389` (`deltas`, pure function) | The 3D measure readout **calls this** — its docstring says it is the single source of truth; do not re-implement the formulas |
| World frame: `LayoutJob.to_world` → (N, E, Z); pins arrive in the viewer as `(e, n, z)` — viewer **x = Easting, y = Northing, z = elevation** | `fieldstitch.py:785`, `gui/tab_fieldstitch.py:761` | So ΔE = x₂−x₁, ΔN = y₂−y₁; measure numbers automatically match staked reality (deltas are translation-invariant, and rotation/scale were already applied by `to_world`) |
| Geometry: `Segment(a,b,radius)`, `Face(pts)` convex quads/octagon rings, `tube_faces()` pipe prisms; painter's-algorithm canvas renderer, zero-idle fx rule, adaptive LOD | `bim.py`, `gui/bim3d.py` | Faces are already convex polygons — Sutherland–Hodgman output draws directly as `create_polygon`; **no triangulation needed for display**, only for picking |
| Test pattern: plain-python, headless math half + xvfb GUI half in one script | `tests/test_bim.py` | Extend this file; same runner idiom |

### 1. Industry norms & expectations

What users trained on mainstream BIM coordination / model-review viewers expect:

- **Section box is THE interrogation tool.** Axis-aligned by default (rotated section boxes are an advanced, rarely-used extra). One box, six faces, each face independently draggable via an on-geometry **handle at the face center** (arrow/square gizmo), plus a one-click reset to full model. Slider panels exist in some viewers but handles are the trained muscle memory.
- **Capping is optional and frequently absent.** Light/web viewers routinely show hollow cuts; true caps require closed solids and loop stitching. Planloom's geometry (single wall quads, open prisms) is **not solid**, so caps are geometrically undefined here — skipping them is the honest, industry-consistent call. The accepted substitute is making the cut *legible*: a highlighted cut-plane rectangle (the repo's existing "glowing cut plane" idiom) on whichever face was last moved.
- **Snap priority is universal: vertex > edge > face**, with a screen-space aperture of roughly 8–14 px (classic CAD default aperture ≈ 10 px; the repo's 12 px is fine). The cursor shows a distinct marker per snap type (square = vertex, diamond = edge point, plain crosshair/dot = face hit). Snapping picks **through** geometry in wireframe-style viewers (no occlusion test) — users expect that; face picks resolve front-most by ray t.
- **Measure reports surveying triple HD / VD / SD** (horizontal, vertical, slope distance — exactly what a total-station data collector displays), plus signed coordinate deltas. Construction users additionally read pipe slope as inches-per-foot. A live rubber band after the first pick, and the readout anchored mid-tape, are standard.
- **Determinism**: same camera + same click ⇒ same pick. All the math is pure; only the last few pixels are tk.

### 2. Build recipe (minimal scope)

#### 2.1 Geometry kernel additions — `bim.py` (pure numpy/stdlib, GUI-free)

**(1) `screen_ray(cam, sx, sy, w, h) -> (origin, dir)`** — inverse of `project_points`, branching exactly like it does:

```text
eye, right, up, fwd = _basis(cam)
half = tan(radians(max(fov,1))/2)
if ortho:
    k   = (h/2) / max(|dist|*half, EPS)          # px per world unit
    origin = eye + right*((sx - w/2)/k) + up*((h/2 - sy)/k)
    dir    = fwd
else:
    f   = (h/2) / half
    dir = right*((sx - w/2)/f) + up*((h/2 - sy)/f) + fwd
    dir = dir / |dir| ; origin = eye
```
Note the y sign flip `(h/2 - sy)` — screen y is down. Depth of any hit for sorting = `dot(hit - eye, fwd)`, matching `project_points`' depth column.

**(2) `ray_triangles(origin, dir, v0, v1, v2) -> (t, u, v) arrays`** — Möller–Trumbore, vectorized over N triangles, **no backface culling** (test `|det| > 1e-12`, not `det > 0` — Face winding is arbitrary and faces are viewed from both sides):

```text
e1 = v1-v0; e2 = v2-v0
p  = cross(dir, e2);  det = dot(e1, p)
miss where |det| < 1e-12
inv = 1/det; tv = origin - v0
u = dot(tv, p)*inv          ; miss where u < -eps or u > 1+eps
q = cross(tv, e1)
v = dot(dir, q)*inv         ; miss where v < -eps or u+v > 1+eps
t = dot(e2, q)*inv          ; miss where t <= 1e-9   (behind the eye)
```
Faces are convex polygons → fan-triangulate on the fly: tris `(p0, pi, pi+1)` for i in 1..n-2. A tiny `fan_tris(poly)` helper is enough.

**(3) `clip_segment_box(a, b, mn, mx) -> (a', b') | None`** — parametric slab clip (Liang–Barsky in 3D). Exact, and a segment against a convex box yields **at most one sub-segment, never more pieces**:

```text
t0, t1 = 0, 1; d = b - a
for k in 0..2:
    if |d[k]| < 1e-12:
        if a[k] < mn[k]-eps or a[k] > mx[k]+eps: return None
    else:
        ta = (mn[k]-a[k])/d[k]; tb = (mx[k]-a[k])/d[k]
        if ta > tb: swap
        t0 = max(t0, ta); t1 = min(t1, tb)
        if t0 > t1: return None
return (a + t0*d, a + t1*d), plus flags (t0>0, t1<1) marking CUT endpoints
```

**(4) `clip_poly_box(pts, mn, mx) -> list`** — Sutherland–Hodgman against the 6 half-spaces in sequence. Inside test per plane is one comparison (`p[k] >= mn[k]-eps` / `p[k] <= mx[k]+eps`); crossing intersection is linear interpolation on axis k. Facts to design around:

- Clipping a **convex** polygon by one plane yields a convex polygon with **at most n+1 vertices**; after all 6 planes a quad is ≤ 10 vertices, an octagon cap ≤ 14. Every Face in the model is convex, so S-H is exact here.
- **Extra triangles**: none for display (canvas draws the clipped polygon directly). For picking, fan triangulation gives `m−2` tris, so a cut quad costs at most 8 tris instead of 2. Only boundary-crossing faces grow; a typical cut crosses a small fraction of faces — expect **< 10–20 % polygon/triangle growth**, and add a trivial pre-pass (face AABB vs box: fully-inside → passthrough, fully-outside → drop) so S-H runs only on straddlers.
- Filter output: drop results with `len < 3` or near-zero area (`|cross-sum| < 1e-12`) to avoid slivers that break the flat-shade normal.

**(5) `measure3d(a, b) -> dict`** — thin adapter over `fieldpro.deltas`: call `deltas((a_y, a_x, a_z), (b_y, b_x, b_z), ts="-")` (viewer x=E, y=N; pass `ts` so the record is deterministic), then read `dn, de, dz, hd, azimuth, cut_fill`; add `sd = hypot(hd, dz or 0)`, `vd = dz`, and `slope_in_per_ft = 12*dz/hd` when `hd > 0.01`. One import direction only (`bim`/GUI → `fieldpro`); `fieldpro` never imports `bim`, so no cycle.

#### 2.2 Section box — `gui/bim3d.py`

- **State**: `self.section = None | {"mn": [x,y,z], "mx": [x,y,z]}`. "Section" button toggles; on enable, initialize to `model.bounds()` padded by 0.5 % of span (the inclusive-eps guarantee below makes the pad belt-and-braces, not load-bearing).
- **Clip cache** (the perf design): clipping is view-independent. Compute `(clipped_segments_with_cut_flags, clipped_faces)` once, keyed by `(id(model), box tuple, frozenset(hidden_systems))`; orbit/pan/zoom re-renders **from the cache**. Invalidate on box drag, model change, legend toggle. This keeps the zero-idle / fast-orbit promises; LOD decimation applies to the *clipped* list as today.
- **Ordering in `_render`**: filter hidden systems → section clip (cache) → slope exaggeration on drawn copies → project. Slope exaggeration stays a pure display distortion; the box, like the Horizon Slice today, cuts on TRUE z.
- **Handles**: with the box active, draw the 12 box edges as thin accent lines plus **6 face-center handles** (small filled squares, tag `boxface:k`). Drag converts screen motion to an axis move:

```text
c  = face center (world); axis = unit outward normal (±x/±y/±z)
u  = project(c + axis) - project(c)        # screen px per world unit, 2-vector
if dot(u,u) < 4: ignore drag               # axis is end-on to the camera (norm: unusable handle)
move = dot((ex-x0, ey-y0), u) / dot(u,u)   # world units along axis
```
Clamp so `mn[k] + 0.01*span <= mx[k]`. Double-click a handle → reset that plane to the model bound; "Section" off → full model. Highlight the last-moved plane with the existing glowing-cut-plane idiom (accent, `stipple="gray12"`). No sliders needed — the Horizon Slice slider can simply drive `mx[2]` when the box is active (open question 1).
- **Everything obeys the box**: segments, faces, pipe prisms (clip the *centerline*, then `tube_faces` the clipped run — do not S-H 10 prism faces per pipe), sheet planes and pins (centroid in/out test is fine for those two — they are markers, not geometry), and the measure/pick candidate set.

#### 2.3 Picking — `bim3d._pick(x, y) -> hit | None`

Assemble in the GUI (visibility state lives there), primitives in `bim.py`:

1. Build candidate sets from the **same visible/clipped/drawn geometry the frame renders** (reuse the clip cache), keeping `(true_pt, drawn_pt)` pairs exactly like `_snap_endpoint` does today.
2. **Vertex pass** (screen space): project all drawn endpoints once (one `project_points` call, repo style); nearest with `dist_px <= 12` and `depth > 0` wins. Ties by smaller depth. *Only original model endpoints count as vertices* — endpoints manufactured by the clip carry the CUT flag and are excluded from vertex snap (they are not real geometry).
3. **Edge pass** (screen space): 2D point-to-segment distance against the projected drawn segments, vectorized (`t = clamp(dot(p-a, b-a)/|b-a|², 0, 1)`); nearest with `dist_px <= 6` wins; hit point = 3D interpolation at `t` on the TRUE segment; depth = interpolated. This covers pipes (their centerline Segment is the pick proxy even in shaded mode — cheaper and steadier than octagon faces).
4. **Face pass** (world space): `screen_ray` + `ray_triangles` over fan-triangulated visible clipped faces; front-most `t` wins; hit = `origin + t*dir`; TRUE point = same (faces are never slope-exaggerated; only pipes are, and pipes are handled by pass 3).
5. Priority is strict: vertex > edge > face. Return `{"kind", "true_pt", "drawn_pt", "depth"}`. No occlusion test for vertex/edge snaps (picks through, the norm for wireframe viewers) — say so in the hint line.
6. Marker convention on the overlay: square = vertex, diamond = edge, small circle = face.

#### 2.4 Measure-in-3D — upgrade of the existing mode

Keep the two-click / third-click-clears / Esc-exits shell and the `(true, drawn)` discipline. Changes:

- `_measure_click` uses `_pick` instead of endpoint-only `_snap_endpoint` (which it replaces).
- After the first pick, draw a **rubber band** to the cursor on `<Motion>` — bound only while measuring, unbound on exit (fx rule: no free-running loop; motion events only).
- Label (all from `bim.measure3d`, i.e. `fieldpro.deltas` numbers; `fmt_ftin` for the trade-facing distances, decimal feet 2-dp for ΔN/ΔE to match the Fieldstitch HUD):

```text
SD 12'-4 1/2"   HD 12'-0"   VD 2'-3"
ΔN +10.00  ΔE −5.00  az 153.4°  slope 2 1/4"/ft
```
Two `create_text` lines on one chip, same panel/accent styling as today. VD is signed via the existing ΔZ; `cut_fill` string is available free if the ResolutionBoard/QA folks ever want C/F phrasing. Because everything sits in the Fieldstitch world frame (x=E, y=N, feet), these numbers agree with `fieldpro`'s ledger to the last digit by construction — that is the acceptance test, not a hope.

#### 2.5 Order of work

1. `bim.py`: `screen_ray`, `ray_triangles` (+ scalar reference), `fan_tris`, `clip_segment_box`, `clip_poly_box`, `measure3d`. Headless tests alongside.
2. `bim3d.py`: `_pick` (replace `_snap_endpoint`), measure label upgrade. GUI construct test.
3. `bim3d.py`: section box state + clip cache + render integration + handles. Construct test.
4. Decide Horizon Slice unification with the owner (open question 1); wire or leave.

### 3. Sharp pitfalls

- **`project_points` clamps behind-camera depth to `_EPS`** — the ray/pick code must not inherit that idiom; a hit with `t <= 0` is a miss, full stop.
- **Screen y is down**: the ray's up-component is `(h/2 − sy)/f`. Get the sign wrong and every pick mirrors vertically — the round-trip test (§4) is the guard.
- **Möller–Trumbore with backface culling (`det > eps`)** silently makes half the walls unpickable depending on orbit side. Use `|det|`.
- **Ortho vs perspective**: ray *origin* varies with the pixel in ortho, ray *direction* varies in perspective. Branch exactly like `project_points`; test both.
- **Inclusive clipping eps**: geometry exactly on a box plane (the default box IS the model bounds) must survive. Use `±1e-9`-slack inside tests, and the "box at bounds ⇒ identical scene" acceptance test as the tripwire — this is the same class of bug as the repo's SEARCH_PAD/occ rounding lesson.
- **Cut endpoints are not vertices.** Letting vertex snap grab a clip-manufactured endpoint reports a measurement to a point that doesn't exist in the model. Flag them in `clip_segment_box` and exclude (edge snap still reaches them, honestly, as points *on* an edge).
- **S-H slivers**: near-tangent clips emit < 3-vertex or zero-area polygons; the flat-shade normal then degenerates. Filter on output.
- **Pipe prisms**: clip the centerline then rebuild `tube_faces` — clipping the 10 prism polygons independently produces a visibly shredded pipe end and 5× the work.
- **`_render`'s single projection batch uses positional index bookkeeping** (`base = 2*len(line_segs)`...). Clipping changes counts per frame; keep the "build list, then index" discipline rigorously or the pins/planes silently read the wrong rows — this is the most likely real bug in the whole track.
- **Handle end-on to the camera** (`|u|² < 4 px²`): dividing by it flings the plane to infinity on a 1 px drag. Skip the drag (and dim the handle), the way mainstream viewers disable the toward-camera arrow.
- **Slope exaggeration**: pick on DRAWN geometry, report TRUE geometry — the repo already has the `(true, drawn)` pair pattern in `_snap_endpoint`; keep it or the tape lies exactly when the user most needs it (exaggerated pipe slopes).
- **fx house rules**: no new `after` loops. The rubber band renders on `<Motion>` only; handle drags render on `<B1-Motion>`; quality `"off"` never animates the box.
- **Import direction**: `gui/bim3d.py` → `fieldpro` is fine (lazy import like the existing `from ..draft import fmt_ftin`); never the reverse.

### 4. Acceptance criteria (deterministic, offline, no display for the math)

Extend `tests/test_bim.py` (same headless + xvfb split):

1. **Ray round-trip**: for a seeded grid of world points × cameras (yaw/pitch/dist varied, both `ortho` states), `project_points` the point, `screen_ray` at the resulting pixel, assert point-to-ray distance `< 1e-6 × depth`.
2. **Möller–Trumbore**: unit-triangle hit at the barycenter gives `u ≈ v ≈ 1/3`; outside points miss; parallel ray misses; hit behind origin (`t<0`) misses; reversed winding still hits (`|det|`); vectorized result equals a scalar reference on 100 seeded rays.
3. **Segment clip**: fully-inside unchanged (bitwise-equal floats); fully-outside `None`; one-plane crossing lands the endpoint on the plane to `1e-9` with the CUT flag set; axis-parallel outside-slab segment `None`; zero-length inside kept.
4. **Polygon clip**: quad fully inside returns 4 identical vertices; quad straddling one plane returns 5 vertices whose shoelace area equals the analytic clipped area to `1e-9`; quad fully outside returns `[]`; vertex count `≤ n + planes_crossed`; degenerate slivers filtered.
5. **Box-at-bounds invariance**: with the section box set exactly to `model.bounds()` on `demo_building()`, the clipped segment/face lists equal the unclipped lists.
6. **Measure parity**: `bim.measure3d(a, b)` fields `dn/de/dz/hd/azimuth` equal `fieldpro.deltas((aN,aE,aZ),(bN,bE,bZ))` to `1e-12` on seeded pairs; `sd² = hd² + vd²`; azimuth of due-east delta = 90.0.
7. **Pick determinism**: fixed camera + unit cube: `_pick`-level math at the projected pixel of a vertex returns that vertex; at a face center with no vertex/edge within radius returns a face hit whose point is on the plane to `1e-9`; a vertex 10 px from a face-hit pixel wins over the face (priority).
8. **GUI construct (xvfb)**: enable section box → handles exist (`find_withtag("boxface:...")`); `event_generate` a handle drag → drawn segment count decreases; measure two clicks on the demo model → label text contains `HD`, `VD`, `SD` and a `°`; Esc clears; `_lod == 1.0` after idle; no new persistent `after` callbacks registered.

### 5. SKIP list (honest)

- **Caps / section fills** — geometry is open quads and prisms, not solids; caps are undefined. The highlighted cut-plane rectangle is the honest substitute. Revisit only if a solid part model ever exists (same reasoning as the Backcheck's GD&T SKIP).
- **Rotated / non-axis-aligned section box, multiple boxes, saved box presets** — axis-aligned single box is the industry default posture; everything else is authoring-tool territory.
- **Measurement chains / running totals, angle dimensions, area-in-3D** — chains and angles are drafting features (the Loft owns dimensioning); area-in-3D needs a face-loop selection UX and duplicates what `reckoner` does properly in 2D takeoff. All three are bloat here.
- **Persistent 3D measurement annotations** — the tape is a transient readout; persistence belongs to markups (2D) if anywhere.
- **Occlusion-aware snapping / z-buffer** — painter's canvas has no z-buffer; picking through is the wireframe-viewer norm. Say it in the hint, don't build it.
- **Extra snap types (midpoint, intersection, perpendicular)** — vertex/edge/face covers field measuring; add midpoint later only if users ask.
- **Clipping the ground grid, pins, sheet planes with S-H** — in/out tests suffice for markers.

### 6. LOC estimate

~750 total: `bim.py` +200–240 (ray, M-T vectorized, fan, two clippers, measure3d), `gui/bim3d.py` +230–280 net (box state/cache/handles/render integration ~150, `_pick` replacing `_snap_endpoint` ~80, label upgrade ~30), `tests/test_bim.py` +280–320. No new files needed except possibly none at all.

### 7. Open questions

1. **Unify the Horizon Slice with the box?** Recommended: the slider drives the box `z_max`, retiring the centroid-cull path (one clip mechanism, visibly better cuts). But the centroid rule is documented as a deliberate cheap choice — owner sign-off needed since the slice's rendering changes.
2. **Face clipping at low fx quality tiers**: keep real S-H everywhere (cache makes it cheap), or fall back to centroid culling at quality `"off"` to honor the old-hardware promise? Recommended: real clip everywhere, cache carries it; measure on old hardware, then decide.
3. **Measure during walk mode**: currently measure and walk are separate modes; enabling picks while walking is free with `screen_ray` (perspective branch). Worth wiring, or keep modes exclusive?
4. **Units toggle** (ft-in vs decimal feet) for survey-leaning users on the measure chip — trivial, but is it wanted?
5. **Snap radius constants**: keep 12 px vertex / add 6 px edge, or expose in prefs (`~/.planloom`)? Recommended: constants first.


# Appendix D — clash-lite interference checks

## Track 4 — Clash-Lite: deterministic interference checks

Pipe runs vs walls/slabs and pipe vs pipe, computed exactly (no meshes, no BVH, no
sampling luck), reported through the Backcheck's Finding format, highlighted in the
existing 3D viewer. Everything below is grounded in the actual repo code:
`pipewright.py` (runs carry `system`, `dia_in`, `invert_ft`, `slope_in_ft`;
`network()` merges vertices within `MERGE_TOL_FT = 0.05`; `to_bim()` already turns
runs into 3D `bim.Segment`s with `radius = dia_in / 24`), `draft.py` (walls are
2-point centerlines with `props["thick_in"]`, model space = decimal feet, y-up =
Fieldstitch world frame E=x N=y; `to_bim(wall_height=10.0)`), `bim.py`
(`Segment.radius`, `Model`, viewer `set_pins`), and `backcheck.py` (rule registry,
`Finding`/`Report`, severity ladder, the honest `SKIPPED_RULES` — which today skips
`STD-SLEEVE` *because* "proving a sleeve at every wall penetration needs
MEP-vs-structure clash data". Clash-Lite is exactly that data; this track un-skips it.)

### 4.1 Industry norms — what a professional clash workflow gets right

Coordination practice (BIM execution plans, trade-coordination meetings) has settled
on a small vocabulary and a small set of report disciplines:

| Norm | Content | Clash-Lite mapping |
|---|---|---|
| **Hard clash** | Two objects physically occupy the same volume. | `capsule ∩ box` / `capsule ∩ capsule` with penetration > ignore threshold. |
| **Soft / clearance clash** | Objects don't touch but one intrudes into a buffer the other needs (service access, insulation, code clearance). | Single global `clearance_in` knob (default **0 = off**; per-discipline clearance tables are SKIPPED — see 4.10). |
| **Duplicate** | The same element modeled twice (near-coincident, same system). | Same-system, near-parallel, near-coaxial overlapping runs → one info finding, suppressing the hard clash it would otherwise spam. |
| **Ignore-below tolerance** | BEPs specify a threshold (commonly on the order of **1/2 in**) below which penetrations are noise, not coordination issues; tolerance-too-tight is the #1 source of false-positive spam. | `hard_ignore_in = 0.5` default, basis string "common coordination convention — verify against the project coordination plan" (house style: every threshold carries its basis, like `MIN_SLOPE`). |
| **Grouping** | Raw pairwise results number in the thousands; reports group them into clash *clusters* (same element pair / same location), one issue each. | One finding per **unordered entity pair**, carrying hit count, worst penetration, and the worst hit's location. |
| **Location reporting** | Every clash cites elements, systems, penetration distance, and a findable location: grid reference + level/elevation. | Detail text: systems + diameters, overlap in trade-fraction inches (`fmt_dia_in`), (x, y) in feet-inches (`fmt_ftin`), Z elevation, nearest grid intersection when the Loft has `grid` entities. |
| **Severity ordering** | Gravity systems coordinate first (they cannot re-slope freely); gross burials outrank grazes; clearances below hards. | major for hard clashes (escalate to blocker when overlap ≥ half the smaller diameter — a gross bury), minor for clearance, info for penetrations/duplicates/concealed runs. |
| **Expected penetrations are not clashes** | A pipe crossing a wall transversely is *supposed to happen* (it gets a sleeve/firestop); flagging it as a hard clash buries real problems. | Transverse wall crossings classify as **penetration** (info, "sleeve — verify"), which is what lets `STD-SLEEVE` graduate out of `SKIPPED_RULES`. |

Sources for the norms row set: [hard/soft/workflow clash taxonomy](https://www.enginero.com/blogs/bim-clash-detection-hard-soft-workflow-clashes/), [clearance-tolerance clash tests and false-positive control](https://designsyncstudio.com/clash-detection-in-bim-tolerances-reports-and-issue-resolution/), [hard vs soft clash definitions](https://www.stonehaven.ae/insights/hard-clash-soft-clash-detection), [clash grouping / best practice](https://pinnacleinfotech.com/bim-clash-detection/), [general clash-detection overview](https://www.spatial.com/glossary/what-is-clash-detection).

### 4.2 Where it lives

New GUI-free module **`rfi_stamper/clash.py`** (stdlib `math` only — Pipewright is
stdlib-only and clash should match; no numpy needed at this scale). Consumers:

- `backcheck.py`: 3–4 new `@_rule(..., inputs={"pipe"})` rules that call
  `clash.detect(ctx.model)` (cache the result on `_Ctx` like `net()`/`fittings()`).
- `gui/tab_draft.py` `send_to_bim()` / `gui/bim3d.py`: clash hits → `viewer.set_pins()`
  (pins already render stem + glowing head + label; **zero new viewer machinery**).

### 4.3 Geometry sources (exact)

**Pipe capsules.** Each polyline segment of a `kind == "pipe"` entity becomes a capsule
`(A, B, r)` with `r = dia_in / 24.0` (the existing radius convention). Vertex z comes
from the *same* interpolation `pipewright.to_bim` uses — factor that 10-line z-profile
into a shared helper (`pipewright.run_z(ent)`) so the viewer and the clash engine can
never disagree. **Critical**: `to_bim` z is the **invert** (pipe bottom); the capsule
axis is the centerline, so lift `z_axis = z_invert + r`.

Runs with `invert_ft is None` (all pressure systems today — `slope_run` refuses them)
have no real elevation; including them at `base_z = 0` would put every system in one
plane and produce a clash storm. **Honestly exclude them** and count them in the
report stats (`"no_elevation": n`) — the Backcheck's "not checked, and why" promise.

**Wall boxes.** Each `kind == "wall"` 2-point entity is an oriented box: local frame
`u` = unit centerline direction, `n = (-u_y, u_x)`, origin at endpoint `a`
(exactly `backcheck._walls`). Box in local coords: `along ∈ [0, L]`,
`perp ∈ [-half, +half]` with `half = thick_in / 24.0`, `z ∈ [0, H]` with
`H = wall_height_ft` (default 10.0 to match `draft.to_bim`). **Slabs are the same
`Box` primitive** — the math ships slab-ready; what's missing is a slab *data source*
(see SKIP list).

### 4.4 Broad phase — which is right at our scale

Neither a grid nor sweep-and-prune. A Loft model holds tens of walls and at most a few
hundred pipe segments; a flat double loop with a cheap inflated-AABB reject is
O(n·m) ≈ 10⁴–10⁵ float comparisons — microseconds, fully deterministic, ~6 lines:

```
pad = r_a + r_b + clearance + hard_slack
if aabb_a and aabb_b (each inflated by its radius) miss by > clearance: skip
```

Iterate in `model.ents` order with `i < j` pair ordering — determinism for free, no
duplicate pairs. If a model ever exceeds ~2 000 pipe segments, the escape hatch is the
repo's existing floor-cell hash idiom (`pipewright.network.node_at`), *not* SAP —
document that in a comment and stop. Do not build it now (bloat fence).

### 4.5 Narrow phase — exact math

**Capsule vs capsule = segment–segment closest distance vs radius sum.** Use the
standard closed form (real-time-collision-detection literature, "closest point of two
segments"), which handles all degeneracies:

```text
seg_seg(P1, Q1, P2, Q2) -> (dist, s, t, C1, C2):
    d1 = Q1-P1;  d2 = Q2-P2;  r = P1-P2
    a = d1·d1;   e = d2·d2;   f = d2·r
    if a <= EPS and e <= EPS: s = t = 0                    # both points
    elif a <= EPS:            s = 0; t = clamp01(f / e)    # P1Q1 is a point
    else:
        c = d1·r
        if e <= EPS:          t = 0; s = clamp01(-c / a)   # P2Q2 is a point
        else:
            b = d1·d2;  den = a*e - b*b
            s = clamp01((b*f - c*e) / den) if den > EPS else 0.0   # parallel: pick s=0
            t = (b*s + f) / e
            if   t < 0: t = 0; s = clamp01(-c / a)
            elif t > 1: t = 1; s = clamp01((b - c) / a)
    C1 = P1 + s*d1;  C2 = P2 + t*d2
    return |C1 - C2|, s, t, C1, C2

overlap = (r1 + r2) - dist          # > 0 means hard clash; that IS the
                                    # penetration depth to report
witness = (C1 + C2) / 2             # the reported clash location (x, y, z)
```

Parallel segments have a continuum of closest pairs; the `den <= EPS → s = 0` branch
picks one end deterministically — same answer every run.

**Capsule vs box.** Transform the pipe segment into the wall's local frame
(`p_local = ((p-a)·u, (p-a)·n, p_z - z0)`), where the wall is the AABB
`[0,L] × [-half,half] × [0,H]`. Then use the **exact Euclidean signed distance to a
box** — positive outside, negative = interior depth:

```text
sd_box(p, lo, hi):
    q_i = max(lo_i - p_i, p_i - hi_i)        # per axis, i = along, perp, z
    if any q_i > 0:  return sqrt(sum(max(q_i, 0)^2))   # outside
    else:            return max_i(q_i)                  # inside: -(depth to nearest face)
```

The signed distance of a **convex** set is a convex function of the point, and the
segment `P(t) = A + t(B-A)` is affine in `t`, so `F(t) = sd_box(P(t))` is **convex on
[0, 1]**. Minimize it with a fixed-iteration **ternary search** (60 iterations shrinks
the bracket by (2/3)⁶⁰ ≈ 3·10⁻¹¹ — machine precision, deterministic, ~10 lines):

```text
min sd = ternary_min(F, 0, 1, iters=60)
overlap = r - min_sd                 # works in BOTH regimes:
                                     #   grazing outside:  r - dist
                                     #   axis inside box:  r + interior_depth
clash iff overlap * 12 >= hard_ignore_in
```

This one convex search replaces a page of closed-form Voronoi-region case analysis —
the smallest correct algorithm. **Do not** reuse the trick on a *union* of boxes (min
of convex functions is not convex); run it per box.

**Penetration vs conflict classification** (in the wall frame, with pipe endpoint
perp-coordinates `n0`, `n1`):

```text
if n0*n1 < 0 and |n0| > half and |n1| > half        # in one face, out the other
   and crossing point (t_c = n0/(n0-n1)) lands with
   along ∈ [-r, L+r] and z ∈ [-r, H+r]:
        -> PENETRATION  (info: "sleeve at wall penetration — verify")
elif |n0| < half and |n1| < half:                    # runs concealed inside the wall
        -> dia_in >= thick_in ? WONT-FIT (major)     # cannot physically fit
                              : CONCEALED (info: "verify cavity/blocking")
elif clash (overlap >= threshold):
        -> HARD CONFLICT (major)                     # grazes, diagonal burials,
                                                     # stub-ends jammed in the wall
```

A stub that *ends* inside a wall at a degree-1 network node (fixture rough-in through
a wall) is normal — demote that specific case to the penetration bucket (see open
questions).

### 4.6 False-positive discipline (the "zero on a clean model" contract)

1. **Joint exclusion** — connected pipes always "clash" at their shared fitting by
   construction. Skip any capsule pair that (a) belongs to the same entity, or
   (b) shares a node in `pipewright.network()` (endpoints within `MERGE_TOL_FT`).
   Build a `frozenset((eid_a, eid_b))` adjacency set from `net.nodes[*].legs` once.
2. **Ignore-below** — report only `overlap_in >= hard_ignore_in` (default 0.5 in).
   The threshold applies to *overlap*, never to raw distance.
3. **Duplicate subsumption** — same system + included angle ≤ 2° + inter-axis distance
   < min(r1, r2) + projected extent overlap ≥ 50 % of the shorter segment → one
   `duplicate` info hit; suppress the hard hit for that pair.
4. **Angle-band epsilons** — reuse the `_ANG_EPS = 1e-6` idiom so an exactly-drawn
   perpendicular crossing never falls out of the penetration band via `acos` noise.

### 4.7 Clustering and the report

`ClashHit` (plain dataclass): `kind` (hard | clearance | penetration | concealed |
wontfit | duplicate), `ent_a`, `ent_b`, `system_a`, `system_b`, `dia_a`, `dia_b`,
`overlap_ft` (signed: negative = clear gap for clearance hits), `at` (x, y, z witness),
`seg_ix` pair. Group hits by **unordered entity pair + kind**; each group becomes one
finding carrying `count`, worst `overlap`, and the worst hit's location — that is the
industry "one issue per clash cluster, not per-triangle spam" norm, and grouping by
entity pair automatically merges the adjacent-segment repeats along two snaking runs.
Sort groups by (severity rank, −overlap, ent_a, ent_b) — deterministic.

**Backcheck wiring** (all `inputs={"pipe"}`, category `geometry` except the sleeve rule):

| Code | Sev | Fires on |
|---|---|---|
| `GEO-CLASH-HARD` | major (**blocker** when overlap ≥ ½·min(dia)) | pipe–pipe or pipe–wall hard conflict groups |
| `GEO-CLASH-CLEAR` | minor | gap < `clearance_in` (only when the knob > 0) |
| `GEO-CLASH-DUP` | info | duplicate run pairs |
| `GEO-PIPE-IN-WALL` | major / info | concealed run: won't-fit vs verify-cavity |
| `STD-SLEEVE` | info | **moves out of `SKIPPED_RULES`** — one finding per transverse penetration: "sleeve/firestop at wall penetration — verify against the rated-assembly schedule". When no run carries an invert it must still register a skip note ("no elevations set — clash not evaluated"). |

Every rule's registry `rule` string states its basis and says "verify against the
project coordination plan" (the repo's soft-norm convention; `test_backcheck`
asserts "verify" appears in threshold-based rule text). `Finding.where` stays 2-D
`(x, y)` in Loft model feet (the markup bridge and `loft_finding_points` assume it);
Z goes into the detail text and into the pins bridge. Detail-text template:

```
4" san × 2" dcw hard clash at (12'-6", 8'-0"), Z 3'-2 1/2", near grid B/2:
overlap 1 1/4" (3 spots along p7×p9 — worst shown)
```

using `pipewright.fmt_dia_in` (overlap, diameters), `draft.fmt_ftin` (coords, Z), and
nearest grid intersection from `draft.grid_points(model)` when grids exist (nearest by
Euclidean distance; omit the clause when the model has no grids).

**3D viewer highlight**: `clash.pins(groups) -> [(x, y, z, label, color)]` with
labels `C1, C2, …` (severity order) and `color = backcheck.SEVERITY_COLORS[sev]`;
`tab_draft.send_to_bim()` already builds the combined walls+pipes model — add
`viewer.set_pins(clash.pins(...))` there. Pins glow, carry labels, and depth-sort
today; nothing new in `bim3d.py`.

### 4.8 Tolerance/constant table (each with a basis string, `MIN_SLOPE`-style)

| Constant | Default | Basis |
|---|---|---|
| `HARD_IGNORE_IN` | 0.5 | common coordination ignore-below convention — verify against project coordination plan |
| `CLEARANCE_IN` | 0.0 (off) | soft-clash buffers are discipline/code-specific (SKIPPED); one global opt-in knob only |
| `WALL_HEIGHT_FT` | 10.0 | matches `draft.to_bim` default |
| `DUP_ANGLE_DEG` / `DUP_AXIS_SEP` / `DUP_OVERLAP` | 2.0 / min(r) / 50 % | duplicate = same-system near-coincident modeling |
| `TERN_ITERS` | 60 | (2/3)⁶⁰ bracket ≈ 3e-11 — machine-precision minimum of a convex function |

Recommended minimal clearance default: **0 in (off)**. Rationale: with per-discipline
clearance codes on the SKIP list, any nonzero default fires on tight-but-legal layouts
(parallel mains strapped to one trapeze) and violates the zero-false-positive
contract. Ship the knob; let the user set it per project (1–2 in is a common choice
for insulated lines — say so in the knob's basis string, brands never).

### 4.9 Build recipe (ordered)

1. Factor `pipewright.run_z(ent) -> [z per vertex]` out of `to_bim` (both call it).
2. `clash.py`: `sd_box`, `seg_seg`, `ternary_min`, `WallBox` from wall ents,
   `capsules(model)` (invert + r lift; no-invert runs excluded and counted),
   `detect(model, opts) -> (hits, stats)` with adjacency exclusion + ignore-below +
   classification, `group(hits) -> [ClashGroup]`, `pins(groups)`.
3. `backcheck.py`: `_Ctx.clash()` lazy cache; the 4 rules above; move `STD-SLEEVE`
   from `SKIPPED_RULES` to a real rule (update `test_backcheck`'s skipped-set
   assertion — it pins the exact set).
4. `tab_draft.send_to_bim`: `set_pins` hookup; `tab_backcheck` rows already render
   findings generically — nothing to add beyond the new codes appearing.
5. `tests/test_clash.py` (auto-discovered by `tests/run_all.py` name pattern) +
   `test_backcheck.py` update.

### 4.10 SKIP list (honest, with reasons)

- **4D / time-phased clash** — no schedule-linked geometry exists; out of scope for a
  drafting board.
- **Per-discipline clearance code tables** (electrical working space, duct insulation,
  access zones) — needs equipment/discipline semantics the Loft doesn't carry; one
  global opt-in `clearance_in` knob only.
- **Slab *data source*** — the box math ships slab-capable, but Loft pipes carry no
  modeled risers (z varies only by slope), so a pipe can essentially never cross a
  slab band today; slab boxes without risers are dead code. Revisit when vertical
  segments exist. Register the gap honestly (skip note), don't fake it.
- **Mesh/BVH clash (OBJ vs pipes), curved pipes** — runs are polylines, walls are
  boxes; general triangle-soup clash is a different product.
- **Cross-source clash** (extrude-derived plan walls vs Loft pipes) — needs
  registration between two calibrations; open question, not v1.
- **Clash management workflow** (statuses assigned/approved/resolved, persistence,
  re-run diffing) — the Backcheck report *is* the workflow surface here.
- **Insulation thickness as extra radius** — one line later (`props["insul_in"]`),
  but a default would be a silent guess; skip.
- **Self-clash within one run** — adjacent segments meet at fittings by design.
- **Pipe-to-wall clearance (soft) checks** — pipes legitimately touch, enter, and
  cross walls; only hard conflicts and the classification above are meaningful.

### 4.11 LOC estimate

`clash.py` ≈ 380 (house-style docstrings included) · backcheck rules + un-skip ≈ 90 ·
GUI pins hookup ≈ 15 · `tests/test_clash.py` ≈ 300 · `test_backcheck.py` touch ≈ 10.
**Ship ≈ 485, total ≈ 800.**

### 4.12 Open questions

1. Wall height: keep the single `wall_height_ft` option, or add a per-wall
   `props["height_ft"]`? (The latter touches `draft.add` defaults — owner call.)
2. Pressure systems never get inverts today; add an `elev_ft` prop (set via a Weaver
   command) so dcw/dhw/gas can participate, or leave them honestly excluded in v1?
3. Is the blocker-escalation rule (overlap ≥ half the smaller diameter) the right
   line, or should gravity-drainage involvement escalate instead? Needs owner
   sign-off — it changes report ordering.
4. Stub-ends inside walls (fixture rough-ins): demote degree-1 endpoint-in-wall to
   the penetration bucket, or leave as hard conflict and let the user override?
5. Should `STD-SLEEVE` graduation happen in this track (test churn in
   `test_backcheck.py`) or land as a follow-up once clash soaks?


# Appendix E — IFC-lite import (STEP subset)

# Track 5 — IFC-lite Import: a From-Scratch STEP (ISO 10303-21) Parser Subset

**Goal:** read an IFC file (the open building-model exchange format, ISO 16739, serialized as a STEP Physical File per ISO 10303-21) and place its **walls, slabs and columns** as `bim.Face` + `bim.Segment` geometry in the repo's world frame (x = East, y = North, z = up — the same frame `extrude.py` targets), fully offline, zero new dependencies (stdlib + numpy only; fitz not even needed). One new module, suggested `rfi_stamper/ifclite.py`, shaped exactly like `extrude.model_from_plan`: `load_ifc(path, ...) -> (bim.Model, report)`.

"IFC" and "STEP" are format names and pass the vendor-name policy (HANDOFF.md); never name the authoring tools that produce these files. A loom-registry name for the feature (the way Fieldstitch/Selvage got theirs) is an open question for the owner.

---

## 1. Industry norms — what a professional *partial* importer gets right

Every credible lightweight IFC viewer follows the same contract:

1. **Never crash on unknown entities.** A STEP file routinely contains hundreds of entity types the importer has never heard of (schema drift, exporter extensions). The parser must index *everything* and interpret *only* what it needs — unknown types cost nothing.
2. **Coverage stats, not silence.** "Imported 14 walls, 6 slabs, 3 columns; skipped 3 IfcBooleanClippingResult, 2 IfcFacetedBrep" — per-class counts plus per-skip reasons. Silent partial import is how bad decisions get made off a model.
3. **Units first.** The #1 real-world import bug is a 304.8× or 1000× scale error. Length-unit resolution (SI prefix, or a conversion-based unit like FOOT) must be implemented before any geometry.
4. **Pick the right representation.** A wall typically carries an `'Axis'` representation (a 2-point polyline) *and* a `'Body'` representation (the solid). Taking "the first one" imports a floor plan of sticks. The #2 real-world bug.
5. **Mapped items exist.** Family/type-based exporters put most geometry behind `IfcMappedItem` indirection; an importer that ignores it imports an almost-empty model from those files. (Scoped P2 here — but the coverage report must count them from day one so P1 is honest about it.)
6. **Schema tolerance.** IFC2X3 and IFC4 files must both load. The safe technique: only rely on attribute positions that are stable across schemas (leading `IfcProduct` attributes, see §5), treat everything trailing as opaque.
7. **Right-handed Z-up.** IFC world coordinates are already x/y in plan, z up — they map to `bim` axes with **no flip**. Only the unit scale applies.

---

## 2. The SPF file grammar (ISO 10303-21) — exactly what the parser must accept

### 2.1 File skeleton

```
ISO-10303-21;
HEADER;
FILE_DESCRIPTION((''),'2;1');
FILE_NAME('','2026-01-01T00:00:00',(''),(''),'','','');
FILE_SCHEMA(('IFC4'));
ENDSEC;
DATA;
#1=IFCPROJECT('0YvctVUKr0kugbFTf53O9L',#2,'Proj',$,$,$,$,(#20),#7);
...
ENDSEC;
END-ISO-10303-21;
```

- `FILE_SCHEMA` carries the schema id: `'IFC2X3'`, `'IFC4'`, `'IFC4X1'…'IFC4X3_ADD2'`. Read it, report it, **attempt import regardless** (warn on unknown).
- **There is no line-continuation character.** Records simply span lines; the only terminator is `;` at top level. The 80-column limit from Part 21 edition 1 is dead — ignore line structure entirely and scan the byte stream. Multiple records on one line are legal.
- Comments `/* ... */` may appear between any two tokens.
- Edition-3 features (anchors, multiple/named DATA sections, references) essentially never appear in IFC — parse the first `DATA; ... ENDSEC;` region and record anything else as skipped.
- **Complex (multi-leaf) instances** `#1=(A(...)B(...));` are legal Part 21 but do not occur for IFC entities — detect the `(` after `=` and record-skip.

### 2.2 Record and argument grammar (what the recursive-descent arg parser handles)

```
record   = "#" INT ws "=" ws KEYWORD ws "(" arglist ")" ws ";"
arglist  = [ arg ( "," arg )* ]
arg      = "$"                      -- unset            -> None
         | "*"                      -- derived          -> sentinel "*"
         | INT | REAL               -- 3000, 1.0E-005, "1." (trailing dot legal)
         | "'" chars "'"            -- string, see 2.3  -> str
         | "#" INT                  -- instance ref     -> Ref(id)
         | "." NAME "."             -- enum / .T. .F. .U.  -> Enum(str)
         | "(" arglist ")"          -- list (nests)     -> list
         | KEYWORD "(" arglist ")"  -- typed value, e.g. IFCLENGTHMEASURE(0.3048)
         | '"' hex '"'              -- binary — record-skip, never used here
```

Typed values (select wrappers) matter: `IfcMeasureWithUnit(IFCRATIOMEASURE(0.3048), #x)` puts an entity-keyword *inside* an argument list. Represent as `Typed(name, args)`, and when a number is expected, unwrap one level.

### 2.3 String encoding quirks (all of these occur in production files)

| Sequence | Meaning |
|---|---|
| `''` | one literal apostrophe |
| `\\` | one literal backslash |
| `\X\hh` | one 8859-1 byte, hex |
| `\X2\ 4-hex groups \X0\` | UTF-16BE run (the common one — non-ASCII names) |
| `\X4\ 8-hex groups \X0\` | UTF-32 run |
| `\S\c` | `chr(ord(c)+128)` in the current code page |
| `\PA\`…`\PI\` | selects the 8859 code page for subsequent `\S\` |
| raw bytes ≥ 0x80 | **non-conforming but common**: exporters dump UTF-8 straight in. Tolerance rule: read the file as latin-1 for structure; when decoding a string, if it contains raw high bytes, attempt UTF-8 re-decode of those bytes, fall back to latin-1. |

Critical scanner rule: strings may contain `;`, `(`, `)`, `#`, `,` — **a naive split on `;` corrupts the index.** The indexer must be a state machine (in-string / in-comment / depth counter).

### 2.4 Two-phase, lazy parse (the memory- and unknown-entity-safe architecture)

```
Pass 1 (index): one O(n) left-to-right scan over the decoded text.
  state: in_string, in_comment
  at each top-level ';' close a record; match head r"#(\d+)\s*=\s*([A-Z0-9_]+)\s*\("
  -> index[id] = (TYPE, args_text_span)          # raw text, NOT parsed

Pass 2 (on demand): args(id) parses the span with the recursive-descent
  grammar of 2.2, memoized.  Only entities reachable from the product
  closure are ever parsed — unknown entities are free, forward references
  (legal in Part 21) are free, and a 100 MB file only pays for its walls.
```

Guardrails: read the whole file into memory with a size cap (suggest 200 MB, error above); a `max_products` cap with a logged message, mirroring `extrude.extract_segments`' `max_segments` pattern.

---

## 3. Unit resolution (do this first — the length scale)

Walk: `IfcProject.args[8]` (UnitsInContext) → `IfcUnitAssignment(Units)` → find the unit with `UnitType == .LENGTHUNIT.`:

- **`IfcSIUnit(*, .LENGTHUNIT., prefix, .METRE.)`** — note the first argument is a literal `*` (derived Dimensions) — the parser MUST accept `*`. Scale to metres = prefix multiplier: `.MILLI.`→1e-3 (the overwhelmingly common case), `.CENTI.`→1e-2, `.DECI.`→1e-1, `$`→1.0. Ship the full prefix table (12 entries, one dict).
- **`IfcConversionBasedUnit(dims, .LENGTHUNIT., 'FOOT'|'INCH'|…, IfcMeasureWithUnit(TYPED(value), IfcSIUnit(...)))`** — metres per unit = `value × si_scale_of_unit_component`. US files use this constantly (`0.3048` ft, `0.0254` in).

Final scale into the repo's model space (Fieldstitch world frame is decimal feet, per CLAUDE.md's Loft note): `unit_scale = metres_per_ifc_unit / 0.3048` (parameter `target_unit="ft"`, `"m"` accepted). **Apply the scale ONCE, to final vertices** — placements, profile dims and depths are all in project units, and directions are unitless, so a single uniform post-scale is both correct and the smallest code. Missing unit context → default metres with a loud entry in the report, never a crash.

---

## 4. Placement math — `IfcAxis2Placement3D` composition (exact)

```
axis2placement3d(Location L, Axis a, RefDirection r) -> 4x4 M:
    Z  = normalize(a)  if a given else (0,0,1)
    x0 = r             if r given else (1,0,0)   # spec default
    X  = normalize(x0 - (x0·Z)·Z)                # Gram-Schmidt: r need not be ⟂ Z
         (if |x0 - (x0·Z)Z| < eps: x0 = (0,1,0) retry, then (0,0,1))
    Y  = Z × X
    M  = [[Xx Yx Zx Lx],
          [Xy Yy Zy Ly],
          [Xz Yz Zz Lz],
          [0  0  0  1 ]]        # columns are X, Y, Z; translation = L
```

`IfcLocalPlacement(PlacementRelTo, RelativePlacement)` chains: `world(p) = world(p.PlacementRelTo) @ axis2placement3d(p.RelativePlacement)`, root when `PlacementRelTo = $`. **Memoize by placement id** (storeys share one parent chain) and guard cycles (seen-set → skip product with reason `"placement cycle"`). Also compose the `IfcGeometricRepresentationContext.WorldCoordinateSystem` if it is non-identity (it is one more call to the same function; usually identity, but silently ignoring a non-identity WCS shifts the whole model).

2D variant for profile positions — `IfcAxis2Placement2D(Location, RefDirection)`: `X = normalize(r or (1,0))`, `Y = (-X.y, X.x)`, 3×3 homogeneous.

Numpy 4×4 `@` composition throughout — the repo already speaks numpy; no hand-rolled matrix classes.

---

## 5. Minimal entity closure and schema tolerance

**Products imported (P1):** `IFCWALL`, `IFCWALLSTANDARDCASE` (IFC2X3 workhorse; deprecated-but-legal in IFC4 — treat identically), `IFCSLAB`, `IFCCOLUMN`. Extension is a one-line table entry; resist adding more classes until asked.

**The schema-tolerance keystone:** every `IfcProduct` subtype, in *both* IFC2X3 and IFC4, has the same seven leading attributes:

| index | attribute | used for |
|---|---|---|
| 0 | GlobalId | report keys |
| 2 | Name | report / label (this is the free "name-only" metadata) |
| 5 | ObjectPlacement | `IfcLocalPlacement` chain |
| 6 | Representation | `IfcProductDefinitionShape` |

Trailing attributes (Tag, PredefinedType — IFC4 added PredefinedType to IfcWall) differ between schemas; **never index past position 6 on a product** and both schemas parse with one code path.

**Representation selection:** `IfcProductDefinitionShape.Representations` (index 2) is a list of `IfcShapeRepresentation(ContextOfItems, RepresentationIdentifier, RepresentationType, Items)`. Select the one with `RepresentationIdentifier == 'Body'` (fall back: `RepresentationType in ('SweptSolid','Clipping','MappedRepresentation')`); explicitly do NOT take `'Axis'` or `'FootPrint'`. No 'Body' → skip with reason.

**Geometry closure (P1):**

```
IfcExtrudedAreaSolid(SweptArea, Position, ExtrudedDirection, Depth)
  SweptArea:
    IfcRectangleProfileDef(ProfileType, ProfileName, Position2D, XDim, YDim)
        -- corners (±XDim/2, ±YDim/2) about Position2D (centered!)
        -- Position2D optional ($ = identity) in IFC4, mandatory in IFC2X3
    IfcArbitraryClosedProfileDef(ProfileType, ProfileName, OuterCurve)
        OuterCurve: IfcPolyline(Points -> IfcCartesianPoint(coords)) only
        -- drop a repeated closing point equal to the first (within 1e-9)
  Position:  IfcAxis2Placement3D (solid's own frame), may be $ in IFC4 -> identity
  ExtrudedDirection: IfcDirection (usually (0,0,1); normalize, tolerate any)
  Depth: positive length
supporting: IfcCartesianPoint (2 or 3 coords), IfcDirection (2 or 3 ratios)
spatial:   IfcRelContainedInSpatialStructure(args[4]=RelatedElements,
           args[5]=RelatingStructure -> IfcBuildingStorey, Name=args[2])
           -- OPTIONAL for geometry (the placement chain already carries the
           storey transform); read it only to label products by storey in the
           report.  Files with broken containment still import.
```

**Sweep → mesh (the whole geometry kernel, ~30 lines):**

```
ring2d = profile vertices (u, v)                    # CCW or CW, don't care
ring3  = [ M_solid @ (u, v, 0, 1) for (u, v) in ring2d ]
d      = R_solid @ normalize(ExtrudedDirection)     # rotation only, no translation
top    = [ p + d * Depth for p in ring3 ]
verts  = (object placement chain M_obj) @ all points, then * unit_scale
faces  = bottom ring, top ring, and side quads [b[i], b[i+1], t[i+1], t[i]]
```

**Mapping to the repo model (`bim.py`):** one `bim.Face` per polygon (`system` = `"walls"` / `"slabs"` / `"columns"`; colors reuse the existing muted palette — walls `#9aab9e` like extrude, slabs `#8f9aa8`, columns `#a09080`); wireframe `bim.Segment`s derived from face loops with **shared-edge dedupe on index pairs**, exactly the `load_obj` pattern (per-solid vertex list, faces as index rings, `(min(a,b),max(a,b))` seen-set, first-seen order). `model.systems = [("walls",…),("slabs",…),("columns",…)]` so the Strata legend toggles work unchanged. Convex/concave doesn't matter to the canvas viewer's flat shading; do not triangulate.

**IFC2X3 vs IFC4 differences that matter for THIS subset (complete list):**
1. `IfcWallStandardCase` common in 2X3, deprecated in IFC4 — alias to wall.
2. `IfcParameterizedProfileDef.Position` mandatory in 2X3, optional (`$`) in IFC4.
3. IFC4-only curve/point entities (`IfcIndexedPolyCurve`, `IfcCartesianPointList2D/3D`) and tessellations (`IfcTriangulatedFaceSet`) — P2; in P1 they land in the skip report by name.
4. Trailing attribute counts on products differ — neutralized by the leading-seven rule.
Nothing else in this closure differs.

---

## 6. The coverage-report contract (the honesty layer)

`load_ifc` returns `(bim.Model, report)`; the report is a plain dict, deterministic (products walked in ascending instance id, skip lists sorted):

```python
report = {
  "schema": "IFC4",
  "unit_scale": 0.0032808398950131233,   # project unit -> target unit
  "target_unit": "ft",
  "imported": {"walls": 14, "slabs": 6, "columns": 3},
  "skipped": [        # (id, ifc_class, reason) — every candidate accounted for
      (211, "IFCWALL", "body item IFCBOOLEANCLIPPINGRESULT not supported"),
      (300, "IFCSLAB", "profile IFCCIRCLEPROFILEDEF not supported"),
      (415, "IFCWALL", "no 'Body' representation"),
  ],
  "unsupported_counts": {"IFCBOOLEANCLIPPINGRESULT": 2, "IFCMAPPEDITEM": 5},
  "storeys": ["LEVEL 1", "LEVEL 2"],
  "warnings": ["no IfcUnitAssignment; assuming metres"],
}
```

Rule: **every candidate product ends up in `imported` or `skipped` — the two must sum to the candidate count.** Each product's conversion is wrapped in one `try/except Exception` that converts any error into a skip reason; a malformed wall can never kill the import. `ValueError` is raised only when *zero* products import (mirroring `load_obj`'s "nothing usable" contract), with the skip summary in the message so the user learns *why* ("0 of 23 walls imported: 23 × mapped representation — not supported until P2").

---

## 7. Staged plan (OCR_PLAN pattern: each stage ships green)

**P1 — engine + report (no GUI):** `ifclite.py` with the indexer/arg-parser (§2), units (§3), placement math (§4), extruded rect + polyline-profile sweeps for wall/slab/column (§5), bim mapping, coverage report (§6). Fixtures + tests (§ acceptance). Public API: `load_ifc(path, target_unit="ft", max_products=5000, log=print)`.

**P2 — the three real-world unlocks + GUI:**
1. `IfcMappedItem` → `IfcRepresentationMap(MappingOrigin, MappedRepresentation)` composed with `IfcCartesianTransformationOperator3D(Axis1, Axis2, LocalOrigin, Scale, Axis3)` (uniform scale only; the NonUniform subtype stays skipped). One extra matrix — unlocks family-based exports.
2. `IfcCircleProfileDef(…, Position, Radius)` as a 16-gon (columns are round more often than not).
3. IFC4 `IfcIndexedPolyCurve` with `Segments = $` or all-`IFCLINEINDEX` (straight polyline through `IfcCartesianPointList2D`); any `IFCARCINDEX` → skip with reason.
4. GUI: "Open IFC…" beside the OBJ loader in the Plans & BIM viewer (`gui/bim3d.py` already has `set_model(bim.load_obj(path))` — same three lines), plus the report text in the log pane.
5. Cheap stretch *if the owner wants it*: `.ifczip` (a zip holding one `.ifc` — sniff magic bytes like `core.read_document`, stdlib `zipfile`, ~10 LOC).

No P3. Anything further (booleans, BReps, tessellation, curved geometry) is a new owner decision, not scope creep.

---

## 8. Pseudocode skeleton (P1 driver)

```
load_ifc(path, target_unit="ft", ...):
    text   = read bytes (size cap), decode latin-1
    schema = parse FILE_SCHEMA from HEADER
    index  = scan_records(text)                    # §2.4 pass 1
    args   = memoized recursive-descent parse      # §2.4 pass 2
    scale  = resolve_length_unit(index, args)      # §3 (+ warning path)
    wcs    = context world-coordinate-system matrix or identity
    for pid in sorted product ids where type in PRODUCT_TABLE:
        try:
            M    = wcs @ placement_chain(args(pid)[5])       # §4, memoized
            rep  = pick_body_representation(args(pid)[6])
            for item in rep.Items:
                if type(item) != IFCEXTRUDEDAREASOLID: skip(reason); continue
                ring = profile_ring(item.SweptArea)          # rect | polyline
                add_solid(model, sweep(ring, item, M) * scale,
                          system=PRODUCT_TABLE[type], name=args(pid)[2])
        except Exception as e: skip(pid, type, str(e))
    if nothing imported: raise ValueError(summary)
    return model, report
```

---

## 9. Sharp pitfalls (each has burned a real importer)

- **`IFCSIUNIT(*,...)`** — the `*` derived-attribute token appears in virtually every IFC file's unit block. An arg parser without `*` dies on file one.
- **String-aware indexing** — strings legally contain `;ʼ()#,`; `''` is an escaped quote. Split-on-semicolon parsers mis-index and the failure is downstream and baffling.
- **`'Axis'` vs `'Body'`** — walls carry both; the wrong pick imports stick figures that *look* plausible in plan view.
- **RefDirection not perpendicular to Axis** — exporters emit slightly-off vectors; without the Gram-Schmidt step the rotation matrix is non-orthonormal and geometry shears subtly.
- **RectangleProfileDef is CENTERED** on its 2D Position — corners are ±half-dims, not (0,0)-anchored. Off-by-half-a-wall placement bug.
- **Position optional in IFC4** — `IfcExtrudedAreaSolid.Position` and profile `Position` can be `$` in IFC4; 2X3-shaped code that assumes a placement crashes.
- **Uniform scale ONCE at the end** — scaling profile dims *and* placement translations independently double-scales; scaling directions breaks rotations. Final-vertex scaling is immune.
- **Forward references are legal** — `#5` may reference `#900`. Any single-pass eager evaluator breaks; the lazy two-phase design (§2.4) is immune.
- **Closed-polyline repeated endpoint** — most polyline profiles repeat the first point last; without dedupe you get a degenerate zero-length side quad and a doubled edge.
- **`1.` and `1.0E-005`** — trailing-dot reals and 3-digit exponents are common; use Python `float()`, don't hand-regex the number grammar too tightly.
- **Placement cycles** — corrupt files can make `PlacementRelTo` loops; a seen-set turns an infinite loop into one skip reason.
- **Don't inline the axis convention** — IFC (E,N,Z) maps straight onto bim (x,y,z); resist "helpful" flips. The acceptance fixtures pin this (the Selvage lesson: coordinate order lives in ONE place).
- **Report shape drift** — the coverage dict is a contract the GUI and tests both consume; freeze its keys in the P1 test, like the Backcheck finding format.

---

## 10. Acceptance criteria (deterministic, offline; plain-python test file per repo convention)

`tests/test_ifclite.py`, fixtures as inline Python string constants (hand-authored IFC, ~15–40 records each — no binary blobs, no downloads):

1. **One IFC4 wall, exact vertices.** MILLI units; rect profile XDim=4000 YDim=200, Depth=3000, placement Location (1000, 2000, 0), identity rotation. Assert all 8 vertices in feet to 1e-9: x ∈ {−3.2808398950…, 9.8425196850…}, y ∈ {6.2335958005…, 6.8897637795…}, z ∈ {0, 9.8425196850…}; assert 6 faces, 12 deduped segments, `report["imported"] == {"walls":1,"slabs":0,"columns":0}`.
2. **Same wall as IFC2X3** (`FILE_SCHEMA (('IFC2X3'))`, `IFCWALLSTANDARDCASE`, mandatory profile Position, 2X3 trailing args) → byte-identical geometry to (1); pins schema tolerance.
3. **Rotation:** RefDirection (0,1,0) on the object placement → the profile X-axis lands on world +Y; assert rotated corner values exactly.
4. **Nested placement chain** site→building→storey→wall, each translating (10000,0,0) mm → wall offset by 3×10000 mm; assert composed translation. Include a non-`$` Axis (tilted Z) case for the Gram-Schmidt path.
5. **Unit matrix:** the same wall in a metre file and in a FOOT `IfcConversionBasedUnit` file → identical model to 1e-9; missing unit block → metres + a `warnings` entry.
6. **L-shaped `IfcArbitraryClosedProfileDef`** (6-point polyline, repeated closing point) slab, Depth 150 mm → 8 faces (2 caps + 6 sides), exact vertex asserts, no degenerate side.
7. **Coverage contract:** a file with 2 walls (one swept, one whose Body is `IFCBOOLEANCLIPPINGRESULT`), an `IFCFLOWSEGMENT`, and 20 unknown entity types → imports 1, skips 1 with the exact reason tuple, `unsupported_counts` exact, no exception; `imported + skipped == candidates`.
8. **Grammar torture:** a record whose Name string is `'a;b''c(#5)'`, a `\X2\00E9\X0\` escape (decodes to é), a `/* comment */` mid-record, a record spanning 3 lines, `IFCSIUNIT(*,...)`, `1.0E-005`, and a forward reference — all parse; string round-trips.
9. **Zero-usable file** → `ValueError` whose message contains the skip summary (mirrors `load_obj`).
10. **Determinism:** loading the same fixture twice yields equal reports and equal vertex streams (sorted-id walk).
11. Registered in `tests/run_all.py`; the module imports no networking (covered by the existing `test_offline` sweep).

P2 adds: mapped-item fixture (wall geometry behind `IfcRepresentationMap` + a translated `IfcCartesianTransformationOperator3D`) asserting equality with the direct-geometry model; circle-column 16-gon vertex count; `IfcIndexedPolyCurve` straight-segment slab; GUI construct check (menu entry exists, `set_model` called) in `test_gui_construct` style.

---

## 11. Honest SKIP list — the heart of this track

| Skipped | Why, and what the user sees |
|---|---|
| `IfcBooleanResult` / `IfcBooleanClippingResult` | The clip operand needs half-space/solid CSG — a geometry kernel, not an importer. Sloped-top walls skip with a named reason. (Optional P2+ *owner decision*: import the un-clipped `FirstOperand` flagged `"approximate: clip ignored"` — never silently.) |
| Openings (`IfcRelVoidsElement` / `IfcOpeningElement`) | Same CSG problem. Walls import **without door/window holes** — fine for the wireframe/massing viewer; say so in the report once per file. |
| BReps (`IfcFacetedBrep`, `IfcAdvancedBrep`) and IFC4 tessellations (`IfcTriangulatedFaceSet`, `IfcPolygonalFaceSet`) | Out of P1/P2 scope discipline. (Note honestly: faceted/tessellated → `bim.Face` is mechanically easy, ~40 LOC each — a cheap later stage *if files in the wild demand it*; advanced/NURBS BReps are a hard never.) |
| Curved geometry: arc profiles, `IfcTrimmedCurve`, `IfcCompositeCurve`, `IfcRevolvedAreaSolid`, `IfcSurfaceCurveSweptAreaSolid` (curved walls) | Curve math + tessellation policy for marginal building stock; skip with reason. |
| Materials, colors, styles (`IfcStyledItem`, `IfcMaterial…`) | The viewer's system palette is the repo's visual language; IFC colors would fight the Strata legend. |
| Property sets (`IfcPropertySet`, quantities) | **Name-only policy:** the product `Name` attribute (position 2, free) is imported as the label; everything else skipped. Psets are a data-management feature, not geometry. |
| Georeferencing (`IfcMapConversion`, `IfcProjectedCRS`, `TrueNorth`) | The model imports in project coordinates; anchoring into a Fieldstitch survey frame, if wanted, is a later explicit transform like `extrude.to_world` — not silent CRS math. |
| Non-uniform transforms (`IfcCartesianTransformationOperator3DnonUniform`) | Shear/anisotropic scale on building elements is exporter pathology; skip. |
| Part 21 edition-3 features, complex `(A()B())` instances, binary `"…"` values, ifcXML | Effectively absent from real IFC traffic; record-skip. |
| Beams, doors, windows, stairs, MEP classes | Scope fence — walls/slabs/columns only until the owner asks. The product table makes each addition one line *later*. |
| Writing IFC | Import only. Ever. |

---

## 12. LOC estimate

| Piece | LOC |
|---|---|
| P1 parser: scanner/indexer + recursive-descent args + string decode | 230–280 |
| P1 units + placement/matrix math (incl. 2D placement, WCS) | 90–110 |
| P1 geometry: profiles + sweep + face/edge build | 120–160 |
| P1 driver: closure walk, bim mapping, coverage report | 100–130 |
| **P1 total (`ifclite.py`)** | **≈ 550–680** |
| P1 tests + inline fixtures | 400–500 |
| P2 (mapped items, circle, indexed polycurve, GUI hook, ifczip) | +180–260 (+~150 tests) |

## 13. Open questions

1. Target unit: confirm decimal **feet** default (matches the Loft/Fieldstitch frame) vs metres, and whether P2 should add an anchor transform (ΔE, ΔN, rotation) into a live Fieldstitch job like `extrude`.
2. Loom-registry name for the feature (HANDOFF.md table) — module name `ifclite.py` is format-only and safe; the user-facing name is the owner's call.
3. Boolean base-operand unwrap as a flagged "approximate" P2 option — yes/no?
4. Are `IfcFacetedBrep`/`IfcTriangulatedFaceSet` worth their ~40 LOC each in P2 (they unlock 2X3 legacy exports and IFC4 tessellated exports respectively), or defer until a real file demands them?
5. File-size / product-count caps: 200 MB / 5000 products acceptable defaults?


# Appendix F — the from-scratch PDF reader/merger (pypdf retirement)

# Track 6 — From-scratch PDF reader/merger: the pypdf retirement

Planloom already owns the **writer** half of PDF (minipdf: byte-exact classic-xref documents, WinAnsi text, deterministic content-hash `/ID`, no `/Info`). This track builds the **reader + page-surgery** half so `pypdf` can leave the runtime the same way reportlab and Tesseract did: staged, flag-gated, oracle-tested, then demoted to a dev-box parity oracle.

A live data point in favor: on the current dev container `import pypdf` **crashes outright** — its `_crypt_providers` unconditionally imports the `cryptography` package, whose native `_rust`/cffi bindings are broken. pypdf's crypto path drags optional native deps into what is supposed to be a pure-Python library; the replacement must not repeat that mistake (see §3.5: no in-house crypto at all).

---

## 1. The required surface (call-site inventory — build THIS, nothing more)

Only four runtime modules touch pypdf. The whole retirement is bounded by this table.

| Call site | pypdf API used | What it actually needs |
|---|---|---|
| `merge.py` `_open` | `PdfReader(path)`, `.is_encrypted`, `.decrypt("")` | open file; detect `/Encrypt`; transparently unlock blank-password files; clean `ValueError` otherwise |
| `merge.py` merge/split/rotate | `len(reader.pages)`, `reader.pages[i]`, `PdfWriter()`, `writer.add_page(page)` → returned page `.rotate(deg)`, `writer.add_outline_item(title, page_idx)`, `writer.metadata = None`, `writer.write(f)` | copy whole pages (annotations travel), set `/Rotate` on the copy, write a top-level outline, emit clean deterministic bytes |
| `stamp.py` | `PdfReader(path)` + `PdfReader(BytesIO)` (its own minipdf overlay), `page.cropbox` (`.width/.height/.left/.bottom`), `Transformation().rotate(r).translate(tx,ty)`, `page.merge_transformed_page(ov, op, expand=False)`, writer as above | composite a **known, self-authored** overlay onto arbitrary plan pages under any `/Rotate`, anchored on the CropBox |
| `reports.py` `project_snapshot_pdf` | `PdfReader(BytesIO/path).pages`, `writer.add_page`, `len(writer.pages)`, write via `merge._atomic_write` | concatenation of **minipdf-authored** files only — the easy case |
| `pdfdoctor.py` `is_encrypted` | `PdfReader(path).is_encrypted` | "does the trailer carry `/Encrypt`" — fitz hides owner-locks, this cross-check must survive |
| tests (`test_merge`, `smoke_test`, `test_batch`, `test_resolution`, `test_reb_stamp`, `test_selvage`, `test_draft`, `test_backcheck`, `test_gui_construct`, `test_fieldstitch_pro`) | above + `page.mediabox.width`, `page.get("/Rotate")`, `"/Annots" in page`, annot `/Subtype`/`/Contents` via `.get_object()`, `reader.outline` + `get_destination_page_number`, `pypdf.generic.RectangleObject` (fixture building) | reader facade must expose mediabox/cropbox/rotate/annots; outline assertions can move to `fitz.get_toc()`; fixture building moves to fitz (`page.set_cropbox`) |

Notably **absent** from the surface: text extraction (fitz owns it), form filling, encryption writing, incremental writing, content-stream parsing of *source* pages, linearization. The honest scope is: *lenient reader + object-graph copier + page-tree writer + one overlay compositor*.

Naming: `pypdf` is imported in 4 runtime files; grep shows zero other runtime uses. `batch.py`, `pipeline.py`, `verify.py`, `hyperlink.py` are fitz-only and untouched.

---

## 2. Industry norms — what a professional lenient parser gets right

Every serious reader (the ISO 32000-1 reference behavior plus the de-facto leniency canon established by the major open-source parsers) converges on the same rules:

1. **The xref is a hint, not the truth.** If `startxref` is missing, points at garbage, or an entry's target doesn't begin with the expected `N G obj`, real parsers fall back to a **full-file scan** for `obj` headers and rebuild the table. Later definitions of the same object number win (incremental-update semantics).
2. **`/Length` is a hint too.** Read `/Length` bytes (resolving an indirect `/Length`), then *verify* `endstream` follows (allowing EOL slop). On mismatch, scan for the `endstream` keyword and use the scanned extent. Never scan first — real content legitimately contains the bytes `endstream`.
3. **Offsets are relative to the `%PDF` header**, which may not be at byte 0 (mail gateways and print spoolers prepend junk). Locate the header in the first 1 KB; if a direct offset misses, retry with `offset + header_pos`.
4. **The free list is ignored.** Nobody walks it; type-`f` entries just mean "no object here".
5. **Newest revision wins.** Walk the `startxref`/`/Prev` chain newest→oldest with first-seen-wins per object number and a visited-set loop guard; merge trailer keys the same way (first-seen `/Root`, `/Encrypt`, `/ID`).
6. **Hybrid-reference files** (classic table whose trailer carries `/XRefStm`): the classic section deliberately lists objstm-compressed objects as *free*; the xref stream at `/XRefStm` supplies them. Read the stream section **before** the classic `/Prev`.
7. **Inheritance is mandatory**, not optional: `/Resources`, `/MediaBox`, `/CropBox`, `/Rotate` climb the `/Parent` chain (depth-capped); `/CropBox` defaults to `/MediaBox`; `/Rotate` normalizes to a multiple of 90; box arrays normalize so x0<x1, y0<y1 (producers emit reversed corners).
8. **Never trust `/Count`, never trust `/Type`.** Enumerate pages by walking `/Kids` (a node with `/Kids` is a Pages node even if `/Type` is missing) with a cycle guard.
9. **Robust inflate**: tolerate truncated zlib tails (`decompressobj` + `flush`, keep partial data) and bogus zlib headers (retry raw deflate, `wbits=-15`).
10. **Composited overlays isolate graphics state**: original content wrapped `q…Q` so a dirty CTM/color left by the plan page can't bleed into the overlay, and vice versa; resource merge never renames names inside content you didn't author.

---

## 3. Build recipe

New code lives inside the existing package: `rfi_stamper/minipdf/lex.py`, `parse.py` (reader core), `graph.py` (importer/serializer), `pagemerge.py` (overlay compositor), `io.py` (the `PdfReader`/`PdfWriter`-shaped facade). Flag: `PLOOM_PDF_IO=mini|pypdf` (default `pypdf` until W3), mirroring `PLOOM_PDF_ENGINE`.

### 3.1 Lexer + object parser (the 8 types)

Token classes over a `bytes` buffer with an integer cursor (no streams-of-streams abstraction — plan sets fit in memory; 500 MB is the practical cap, assert it):

- **Whitespace**: `\x00 \t \n \x0c \r ' '`. **Delimiters**: `( ) < > [ ] { } / %`. Comments `%…EOL` are whitespace (except inside strings).
- **Types**: booleans, numbers, literal strings, hex strings, names, arrays, dicts, streams, `null` — plus the indirect reference, which needs **two-token lookahead**: after parsing an integer, peek for `int` `R` (and, at top level, `int` `obj`).
- **Python-native model** (keeps the serializer trivial): `bool/int/float/None`, `bytes` for strings, `Name(str)` subclass, `Ref = (num, gen)` namedtuple, `dict`, `list`, `Stream` (dict + `raw: bytes` — raw is the *undecoded* body).

```text
parse_object(lex):
    tok = lex.next()
    '/'  -> name (decode #xx pairs; invalid '#' kept literally)
    '('  -> literal string: nesting-aware; escapes \n \r \t \b \f \( \) \\,
            \ooo (1-3 octal digits), backslash-EOL = continuation,
            bare CR/CRLF inside -> LF
    '<<' -> dict: keys MUST be names; skip garbage tokens until '/' or '>>'
            (leniency); duplicate key -> last wins
    '<'  -> hex string: ignore whitespace, odd trailing digit padded with 0
    '['  -> array until ']'
    number -> lookahead for "G R" (Ref); accept '+.5', '1.', '--3'->(-3? no: 0
            with a logged warning — match lenient canon), exponent -> float()
    keyword true/false/null
after a dict at top level: peek 'stream' -> read body per /Length-then-verify
    ('stream' followed by LF or CRLF exactly; data; optional EOL; 'endstream';
    on mismatch scan for b'endstream' and take the scanned extent)
```

Recursion depth capped (~256) — a hostile/broken file must raise a clean `ValueError`, never blow the C stack.

### 3.2 Xref: classic tables, xref streams, ObjStm, chains

**Classic table**: `xref` keyword; subsection headers `start count`; entries nominally 20 bytes (`%010d %05d [nf]\r\n`) — but parse them **tokenwise** (three tokens per entry), which transparently survives the notorious 19-byte (single-EOL) and 21-byte producer variants and subsection headers with stray spaces.

**Xref stream** (`/Type /XRef`, PDF 1.5): decode the stream (Flate + predictor, §3.3), then:

```text
W = [w1, w2, w3]; Index = trailer.get(/Index, [0, Size])
row = w1+w2+w3 bytes, fields big-endian unsigned
f1 = 1 if w1 == 0 else int(field1)      # type default is 1
f3 = 0 if w3 == 0 else int(field3)
type 0: free (ignore)   type 1: (offset=f2, gen=f3)
type 2: (in_objstm: stream_obj=f2, index_in_stream=f3)
unknown types: ignore (spec: treat as null reference)
```

**Object streams** (`/Type /ObjStm`): decode once, cache. Header region = `/N` pairs of `objnum offset` integers; objects begin at `/First`; each is a bare direct object (no `obj/endobj`, no streams inside, gen always 0). Parse lazily per lookup, memoize the whole stream's objects on first touch.

**Chain walk**: find `startxref` in the last 2 KB (take the *last* occurrence); loop: load section → merge entries first-seen-wins → if classic trailer has `/XRefStm`, load that stream section *before* honoring `/Prev` → follow `/Prev` with a visited-offset set and a hard cap (~512 revisions). Merge trailer dicts first-seen-wins. `is_encrypted` = merged trailer has `/Encrypt`.

**Object fetch** `get(ref)`: memoized; classic entry → seek, expect `N G obj` (match `N` only — gen mismatches are a known producer quirk), parse, expect-but-don't-require `endobj`; on any failure → **recovery mode** (§3.4) and retry once.

### 3.3 Filters: stdlib zlib + PNG predictors (the only decoders needed)

We never decode *page content* (it passes through verbatim on every path), so the decoder inventory is exactly: **FlateDecode**, needed for xref streams, ObjStm, and reading back our own minipdf overlay stream. Any other filter on a *structural* stream → loud `ValueError` (does not occur in practice; xref/objstm streams are Flate or raw).

```text
inflate(data):
    try: d = zlib.decompressobj(); out = d.decompress(data) + d.flush()
    except zlib.error:
        try raw deflate: zlib.decompressobj(-15)        # bogus zlib header
        else: retry per-byte-truncated tail; keep partial output + warn

unpredict(data, parms):                                  # /DecodeParms
    pred = parms.get(/Predictor, 1); if pred < 10: return data (pred 2 TIFF: refuse)
    cols = parms.get(/Columns,1) * parms.get(/Colors,1) * parms.get(/BitsPerComponent,8)//8
    rowlen = cols + 1; prev = bytes(cols)
    for each row: ft=row[0]; cur=bytearray(row[1:])
        for i in range(cols):
            a = cur[i-1] if i else 0; b = prev[i]; c = prev[i-1] if i else 0
            ft==1: cur[i]+=a   ft==2: cur[i]+=b   ft==3: cur[i]+=(a+b)//2
            ft==4: cur[i]+=paeth(a,b,c)            # all mod 256
        prev = bytes(cur)

paeth(a,b,c): p=a+b-c; pa,pb,pc=|p-a|,|p-b|,|p-c|
              return a if pa<=pb and pa<=pc else (b if pb<=pc else c)
```

Bytes-per-pixel for xref streams is 1 (8-bit single component), so `a` is simply the previous byte — but write the general column math anyway; it is 4 lines and numpy is available if a hot loop ever matters (it won't: xref streams are kilobytes).

### 3.4 Lenient recovery — the scan-rebuild

Triggered when: no `startxref`, an offset misses its object, a chain loops, or a needed object is absent from the merged table.

```text
rebuild(buf):
    header = buf.find(b'%PDF'); base = max(header, 0)
    for m in re.finditer(rb'(\d{1,10})\s+(\d{1,5})\s+obj\b', buf):
        table[int(m[1])] = m.start()        # LAST occurrence wins (newest revision)
    for m in re.finditer(rb'trailer', buf): parse following dict; keep last with /Root
    if no /Root found: scan objects for a dict with /Type /Catalog
```

Also: when a direct offset misses, first try `offset + header_pos` (junk-prefixed file) before full rebuild. Recovery is **all-or-nothing per document** and sets a `doc.repaired` flag — the strict self-check in tests asserts this flag is *False* for anything Planloom itself wrote (guards the MINIPDF_PLAN trap: "fitz/pypdf silently rebuild a broken xref and hide the bug in tests while a strict viewer fails").

### 3.5 Encryption: detect precisely, decrypt via fitz, write zero crypto

Recommendation: **do not implement RC4 or AES in-house.** The ciphers are the cheap part (~60 lines); the expensive, bug-prone part is the plumbing — per-object keys (MD5 of file key + objnum + gen), decrypting every string and stream *except* the xref stream and `/Encrypt` values, `/CF` crypt-filter dispatch, AESV2 CBC + IV, AESV3/R6 key derivation. That is hundreds of purposeful-looking lines serving one edge case — and pymupdf, a permanent runtime dependency, already does all of it.

- `is_encrypted(path)` = new reader's trailer check (`/Encrypt` present). This *strengthens* `pdfdoctor.is_encrypted` (fitz hides owner-locks; the trailer never lies) and drops its pypdf fallback.
- `merge._open` behavior preserved exactly: on `/Encrypt`, open with fitz, `authenticate("")`; on success save `encryption=PDF_ENCRYPT_NONE` to a `tempfile.NamedTemporaryFile` and re-parse that; on failure raise the same `ValueError("... is password-protected; unlock it first")`. ~20 lines, covers RC4-40/128 and AES-128/256 blank-password uniformly, zero crypto code, fully offline.

### 3.6 Page tree walking with inheritance

```text
walk(node=Root/Pages, inherited={}, seen=set()):
    if id in seen: skip (cycle)             # /Kids loops exist in the wild
    inh = inherited + {k: node[k] for k in (/Resources /MediaBox /CropBox /Rotate) if k in node}
    if /Kids in node: for kid in Kids: walk(kid, inh)    # even if /Type missing
    else: yield Page(dict=node, inherited=inh)
```

Per-page accessors: `mediabox` (inherited, corners normalized, floats), `cropbox` (defaults to mediabox, **intersected with mediabox** — out-of-bounds crops occur), `rotation` (`int % 360`, snapped to {0,90,180,270}), `get("/Rotate")`, `"/Annots" in page`. `/Count` is reported but page count comes from the walk.

### 3.7 Writer side: object-graph importer + generic serializer

minipdf's `Document` stays the closed-world *authoring* model. Merging needs a second, generic emitter in `graph.py` — same byte-discipline (classic xref, 20-byte records, exact `/Length`, no `/Info`, content-hash `/ID`), reusing `content.fmt_num` and `encoding.pdf_name`.

**Importer** (deep copy with renumbering), two-pass:

```text
plan(dst, selected_pages):                       # pass 1
    for pg in selected_pages: pagemap[id(pg)] = dst.reserve()

import_val(v, memo):                             # pass 2
    Ref     -> tgt = resolve(v)
               if is_page_dict(tgt):  pagemap.get(id(tgt), NULL)   # unselected page -> null
               if is_pages_node(tgt): NULL                         # NEVER follow /Parent upward
               memo[(doc_id, v.num)] or allocate + recurse
    dict    -> {k: import_val(x) for k,x if k != /Parent-on-page}
    Stream  -> copy dict verbatim (incl. /Filter,/DecodeParms) + RAW bytes untouched
    list/scalars -> rebuild / pass through
```

The `/Parent` cut and the page-typed-ref null rule are **the** load-bearing decisions: without them one copied page's `/Parent → /Kids` drags the whole source file in, and a GoTo annotation's `/Dest` drags in unselected pages. A nulled `/Dest` is a dead link every viewer tolerates. Annot `/P` (optional) is repointed to the new page. Copied streams are *never* re-encoded or re-formatted — raw bytes travel, so untouched pages stay pixel-identical by construction.

**Serializer**: names via `pdf_name` (with `#` escaping), numbers via `fmt_num`, parsed byte-strings **always as hex** (`<...>` — 2 lines, zero escaping bugs, deterministic), all imported objects written gen 0 (renumbering flattens generations — safe and normal). Assemble: body → new `/Pages` (flat `/Kids` — fine at plan-set scale) → `/Catalog` (+ `/Outlines` when bookmarks requested: `/First /Last /Count` and a `/Prev`/`/Next` chain of `<< /Title (…) /Parent … /Dest [pgref /Fit] >>`) → classic xref → trailer with content-hash `/ID`, **no `/Info` ever** (the `writer.metadata = None` dance in merge.py/stamp.py gets deleted, not ported).

`WriterPage.rotate(deg)`: `copy["/Rotate"] = (old + deg) % 360` on the imported page dict.

### 3.8 `merge_transformed_page` equivalent — closed-form CTMs, array-wrap compositing

Skip pypdf's `Transformation` algebra entirely. `stamp._viewer_to_media`'s four field-verified cases collapse to four literal matrices (a `cm` maps `(x,y) → (a·x+c·y+e, b·x+d·y+f)`; `w,h` = **unrotated** CropBox dims, `x0,y0` = CropBox lower-left):

| `/Rotate` | CTM `a b c d e f` | check |
|---|---|---|
| 0 | `1 0 0 1 x0 y0` | identity + crop offset |
| 90 | `0 1 -1 0 w+x0 y0` | viewer `(x,y) → (w−y, x)` — matches the FIELD-VERIFIED gotcha |
| 180 | `-1 0 0 -1 w+x0 h+y0` | `(w−x, h−y)` |
| 270 | `0 -1 1 0 x0 h+y0` | `(y, h−x)` |

Compositing **never decodes plan content**. `/Contents` (single stream or array) becomes an array with two tiny new streams around the untouched originals:

```text
pre  = stream(b"q\n")                                    # isolate plan state
post = stream(b"Q\nq\n" + b"%s %s %s %s %s %s cm\n" % ctm
              + overlay_ops + b"\nQ")
page[/Contents] = [ref(pre), *original_refs, ref(post)]
```

(Spec-legal: a contents array is one logical stream; tokens must not span segments, and ours never do because each segment ends at an operator boundary with a trailing newline.) `overlay_ops` = the inflated content stream of our own minipdf overlay page — we authored it, so its inventory is known: `q/Q, cm, w, re, m/l/c, S/f/B/n, W n, rg/RG, d, BT/ET, Tf, Td, Tj` and `/Fn` font names only.

**Resource merge, without ever rewriting plan content**: materialize the page's effective `/Resources` (inherited copy → the page gets its **own** dict; shared/inherited resources must be copied-on-write or fonts leak across pages), then add the overlay's Type1 font dicts under **fresh keys guaranteed not to collide** (scan existing `/Font` keys; pick `/PLF1…/PLFn`; rewrite our own tiny known stream's `/F1 `→`/PLF1 ` tokens — bounded, safe, ours). `expand=False` semantics = don't touch any page box (the only mode used).

### 3.9 Facade + cutovers

`minipdf/io.py` exposes exactly the shape §1 needs (`Reader(path_or_stream)`, `.pages`, `.is_encrypted`, `.decrypt("")`, page proxies; `Writer()`, `.add_page`, `.add_outline_item`, `.pages`, `.write(f)`, `add_overlay(page, overlay_page, ctm)`). Call sites switch on `PLOOM_PDF_IO`; `reports.py` (all-minipdf inputs) cuts over first, `merge.py` second, `stamp.py` last (verify.py is its safety net), `pdfdoctor.is_encrypted` swaps its cross-check import.

---

## 4. Staging plan (pypdf as the oracle — the reportlab pattern, again)

| Stage | Delivers | Oracle / guard |
|---|---|---|
| **R1** | lexer, object parser, classic xref + trailer + `/Prev`, page tree + inheritance, `is_encrypted` | parse every PDF the test suite produces (harness hook collects them during `run_all.py`) + fitz-saved variants; per file: page count, per-page media/crop box (±1e-4 pt), `/Rotate`, annot count == pypdf's answers |
| **R2** | Flate + predictors, xref streams, ObjStm, hybrid `/XRefStm`, incremental updates | fixtures generated offline with fitz: `doc.save(use_objstms=1)` re-save of every R1 fixture; `saveIncr()` two-revision files (test_merge's annotated fixture already exercises this); same parity assertions |
| **R3** | recovery scan, quirk battery | deterministic byte-surgery fixtures (§5), each must parse to the uncorrupted original's page count + fitz-extracted text |
| **W1** | serializer, graph importer, page tree + outline writer; `merge.py`/`reports.py` behind `PLOOM_PDF_IO` | run `test_merge`/`test_resolution`/`test_selvage` under both backends; fitz pixel-compare merged outputs; fitz `get_toc()` for outlines; strict self-re-parse (recovery disabled, `repaired == False`) |
| **W2** | overlay compositor + CTM table; `stamp.py` cutover | `smoke_test` (rotation 0 + 90) and `test_reb_stamp` (trimmed CropBox) under both backends; outputs render **pixel-identical** at 90 dpi gray via fitz; full `pipeline.run` verify == PASS |
| **W3** | flag default → `mini`; fixture-building in tests moves to fitz (`set_cropbox` replaces `RectangleObject`); pypdf demoted to dev-only oracle (kept importable behind the flag, dropped from `requirements.txt`); HANDOFF/CLAUDE.md truth pass; Windows exe smoke | full `tests/run_all.py` green with pypdf uninstalled; frozen-exe CLI smoke ends in PASS |

---

## 5. The producer-quirk long tail (what leniency must survive)

| # | Quirk | Defense |
|---|---|---|
| 1 | junk bytes before `%PDF` (mail/print-spool wrappers) | find header in first 1 KB; retry offsets `+header_pos` |
| 2 | `startxref` missing / 0 / pointing at the wrong revision or mid-file | last-occurrence search in tail 2 KB; scan-rebuild fallback |
| 3 | whole xref table shifted by a constant (EOL-translated by a text tool) | expect `N G obj` at target; miss → rebuild |
| 4 | 19/21-byte xref rows, spaces in subsection headers | tokenwise entry parsing, never fixed 20-byte slicing |
| 5 | `/Length` wrong, or an indirect `/Length` pointing at a free/absent object | read-then-verify `endstream`; scan on mismatch |
| 6 | hybrid-reference files (`/XRefStm` in a classic trailer; objstm objects marked *free* classically) | read the stream section before the classic `/Prev` |
| 7 | generation-number mismatches (`entry gen ≠ header gen`) | match object number only |
| 8 | duplicate object numbers across revisions / within one body | newest-revision-first, first-seen-wins; rebuild takes *last* body occurrence |
| 9 | dict garbage: non-name keys, duplicate keys, comments between tokens | skip-to-`/` leniency; last duplicate wins; comments are whitespace |
| 10 | numbers `+.5`, `1.`, `--3`, exponents (illegal but shipped) | permissive number lexer, warn-don't-die |
| 11 | strings with raw CR/CRLF, octal escapes, backslash-EOL; odd-length hex strings | §3.1 rules |
| 12 | zlib streams with corrupt headers or truncated tails | raw-deflate retry + partial-flush salvage |
| 13 | page-tree cycles, `/Kids` nodes missing `/Type`, wrong `/Count` | visited set; structure-over-type; count from walk |
| 14 | reversed / out-of-bounds box corners; CropBox larger than MediaBox | normalize corners; intersect crop with media |
| 15 | missing `endobj` / `%%EOF`, trailing garbage after `%%EOF` | resync on next `N G obj`; EOF marker never required |
| 16 | shared `/Resources` or shared `/Contents` between pages | copy-on-write before mutation; array-wrap never mutates the shared stream |
| 17 | unbalanced `q`/`Q` in plan content (excess `Q` can pop the wrapper) | accepted residual risk — pixel verify catches it loudly (invariant 4) |
| 18 | outline titles in UTF-16BE (BOM `FE FF`) vs PDFDocEncoding | decode UTF-16BE on BOM else latin-1 (documented approximation) |

---

## 6. Sharp pitfalls (repo-specific)

- **Do not port pypdf's `Transformation` algebra.** Its `translate()` is a raw `e,f` addition (device-space post-translate), not a matrix pre-multiply — replicating "the API" invites re-deriving the 180°-flip bug the hard way. Ship the four literal CTMs (§3.8); the field-verified behavior is pinned by pixel parity, not by API mimicry.
- **Never re-encode, re-compress, or re-format anything inside a copied stream.** Raw bytes travel; only *dict values* are re-serialized. This is what makes "untouched pages pixel-identical" free.
- **`/Parent` is the graph bomb.** Forgetting the cut imports the entire source document behind every page. Same for `/Dest`/`/P` refs to unselected pages → null them, never chase them.
- **CropBox, not MediaBox, anchors the overlay** — already learned once (`stamp.py` docstring); the new `add_overlay` takes the crop tuple explicitly so the mistake can't recur silently.
- **Resource sharing**: minipdf's own writer shares one `/Resources` across all pages; stamping a *copy* of such a page must COW the dict or font injections bleed between pages and collide.
- **Reader-side `/F1` collisions**: plan pages routinely already define `/F1`; that's why the overlay fonts get fresh `/PLFn` keys with a rewrite of *our* stream only — never rename names in plan content (you'd have to parse it; see SKIP).
- **fitz/pypdf auto-repair hides writer bugs** (MINIPDF_PLAN's hard rule): parity tests that only check "opens in fitz" prove nothing. The strict self-re-parse (recovery disabled) and the `repaired == False` assertion are the real container check.
- **Streams may not be lazily half-read**: an ObjStm consulted for one object should cache all its objects — repeated inflate of a 5 MB objstm per lookup is the classic accidental O(n²).
- **`writer.metadata = None` must not survive as a no-op setter** — delete the dance at the call sites; the new writer structurally cannot emit `/Info` (policy, invariant 7 / NDA posture).
- **BytesIO inputs**: `stamp.py` reads its own overlay from a buffer — the facade must accept path *or* binary stream from day one or W2 stalls.
- **Test fixtures still import pypdf** (`RectangleObject`, fixture writers): sweep them to fitz in W3 or "pypdf-free" is a lie the CI can't see.

---

## 7. Acceptance criteria (all deterministic, all offline)

1. **Corpus parity (R1/R2):** for every PDF written during `tests/run_all.py` (collected by a harness hook) plus its fitz `use_objstms=1` re-save and a `saveIncr()` variant: page count, per-page MediaBox/CropBox (±1e-4 pt), `/Rotate`, and annot count equal pypdf's (oracle present on the dev box only).
2. **Quirk battery (R3):** ≥ 10 byte-surgery fixtures (one per row of §5 rows 1–8, 13, 15, built deterministically in-test from a known-good minipdf file): each parses; page count and fitz-extracted text equal the uncorrupted original; the `repaired` flag is True exactly for the corrupted ones.
3. **Predictor unit vector:** a hand-built 3-row Flate+Predictor-12 xref stream (each of filter types 0–4 exercised, Paeth included) decodes to known bytes.
4. **Merge parity (W1):** `test_merge.py` passes under `PLOOM_PDF_IO=mini` — mediabox widths, `/Rotate` lists, annotation survival (`/Text` subtype + "reviewer note" contents), no-bookmark mode; outline titles/targets verified via `fitz.get_toc() == [[1,"alpha",1],[1,"bravo",4],[1,"Charlie p2",6]]`.
5. **Pixel parity (W1/W2):** every page of merge/split/rotate/stamp outputs renders byte-identical (90 dpi grayscale `pix.samples`) between the two backends on rotation 0/90/180/270 and trimmed-CropBox fixtures; full `pipeline.run` report ends PASS (verify.py untouched and unweakened).
6. **Determinism:** running any merge/stamp twice yields identical output bytes; `b"/Producer"`, `b"/CreationDate"`, `b"/Info"` absent from every output.
7. **Strict self-check:** every output re-parses with recovery disabled, `repaired == False`, and every xref offset lands exactly on its `N 0 obj`.
8. **Encryption behavior:** fitz-written RC4-128 and AES-256 owner-password fixtures open transparently through `merge._open` (page count correct); a user-password fixture raises `ValueError` naming the file; `pdfdoctor.is_encrypted` still detects the owner-locked file fitz opens silently.
9. **Retirement proof (W3):** `tests/run_all.py` fully green in a venv with pypdf uninstalled; `requirements.txt` reads `pymupdf`, `numpy` only.

---

## 8. Honest SKIP list

- **In-house crypto (RC4/AES/all handlers)** — fitz-assisted blank-password decrypt (§3.5); non-blank passwords refused with today's exact error. Public-key/R6 edge handlers: refuse.
- **All decoders except Flate(+PNG predictors)** — LZW, DCT, CCITT, JBIG2, JPX, RunLength, ASCII85/Hex, TIFF predictor 2: never needed because page content is never decoded; loud refusal if ever met on a structural stream.
- **Content-stream parsing/rewriting of source pages** — no operator model, no name renaming inside plan content; the array-wrap makes it unnecessary.
- **Linearization** — never write it; reader ignores hint streams (they parse as ordinary objects and are dropped on import).
- **Tagged PDF / StructTree fidelity** — `/StructParents` numbers ride along as opaque ints; the tree itself is not imported (dangling is universally tolerated).
- **AcroForm semantics** — widget annots travel with pages (appearance streams render); the catalog-level `/AcroForm` dict is not merged (matches current pypdf-path behavior).
- **Incremental-update writing, xref-stream/ObjStm writing** — always emit one complete classic-xref file (minipdf's proven floor).
- **Balanced page trees, name/number tree writing** — flat `/Kids`; named destinations in copied links resolve-to-direct if trivially found in `/Names /Dests`, else null (~25 lines; drop entirely if the owner prefers).
- **General `Transformation` algebra** — four literal CTMs.
- **Text extraction, rendering, repair-heavy surgery** — fitz keeps owning those (pdfdoctor unchanged apart from the `is_encrypted` cross-check import).
- **PDF 2.0 features** (UTF-8 strings honored on BOM; nothing else).

---

## 9. LOC estimate

| Stage | Modules | Core LOC | Test LOC |
|---|---|---|---|
| R1 | lex + parse + classic xref + page tree | ~400 | ~150 |
| R2 | predictors + xref streams + ObjStm + chains/hybrid | ~200 | ~120 |
| R3 | recovery + quirk battery | ~90 | ~140 |
| W1 | serializer + importer + page/outline writer + merge/reports cutover | ~340 | ~120 |
| W2 | overlay compositor + CTMs + stamp cutover | ~150 | ~60 |
| W3 | facade polish, fixture sweep to fitz, docs, dep removal | ~70 (net, incl. deletions) | ~40 |
| **Total** | | **~1,250** | **~630** |

For calibration: minipdf's writer half landed at ~1,900 LOC including 618 lines of font metrics data; a reader half at ~1,250 with no data tables is proportionate.

## 10. Open questions

1. **Name** (HANDOFF registry; "Binder" is taken by the Loft tree). Candidate: **the Shuttle** — the loom piece that carries the thread back and forth across the warp, as this module carries pages between documents. Needs owner sign-off.
2. **pypdf's afterlife**: keep as a dev-box-only oracle behind `PLOOM_PDF_IO=pypdf` indefinitely (the reportlab pattern), or drop the flag after one shipped round?
3. **Blank-password transparency**: keep `merge._open`'s silent fitz-assisted unlock, or surface it in the GUI as an explicit "unlocked a protected file" log line (NDA-posture visibility)?
4. **Named destinations in copied annots**: resolve-to-direct (~25 lines) or null them? (Source-file bookmarks are already dropped by today's merge path — confirmed non-goal.)
5. **Memory ceiling**: whole-file `bytes` parsing is the simple/fast choice; is a hard cap (refuse > ~500 MB with a clean message) acceptable, or do 1 GB+ scan sets exist in the owner's world (would argue for `mmap`, +~15 lines)?
6. **Strict self-check in runtime** (not just tests): re-parse every delivered output with recovery disabled before the atomic rename — ~5 lines, small time cost on huge sets. Worth it as a fifth invariant-4-style guard?


# Appendix G — vector drawing diff + CPM scheduler

# Track 7 — Two Smaller Engines

Two independent, small, pure engines. (A) a from-scratch **vector drawing-revision diff** (proposed registry name: **the Slipsheet** — the light-table slip-sheeting of two vellums; naming is the owner's call via HANDOFF.md). (B) a textbook **CPM scheduler** over the existing `project.ScheduleItem` store (proposed name: **the Tautline** — the taut-line hitch; the critical path is the chain with no slack). Both are fully offline, fitz/numpy/stdlib only, no new packages.

---

## A. Vector Drawing Diff (`rfi_stamper/drawdiff.py`)

### A.1 Industry norms — what a professional revision compare gets right

- **Two comparison idioms coexist** in drawing review: (1) *overlay/slip-sheet compare* — old linework one color, new another, common dark (Planloom already ships the raster version in `align.py`: base-only red `(200,30,30)`, overlay-only blue `(30,80,200)`, both near-black); (2) *change summarization* — reviewers want "what changed and where", i.e. clouded regions with a revision tag, exactly like a real addendum/ASI issue. A professional tool reports **regions, not segments** ("3 change regions"), because a moved wall is one change even if it is 40 segments.
- **Redline stroke conventions**: added/new work **solid**; removed work **dashed** (the demolition-plan convention: existing-to-be-removed is dashed). Clouds go **around** the changed area; the cloud is an attention shape, never the change itself.
- **Registration is the whole game.** Revisions are frequently re-plotted with a sub-point (or worse) shift/rotation; a naive diff then reports the entire sheet changed. Professional compare always aligns first.
- **The classic false-diff sources** are (a) misregistration, (b) *collinear split/merge* — CAD re-exports routinely emit one line as two touching segments or vice versa with zero visual change, (c) hatch/dash re-tessellation, (d) text — which is **not** in `get_drawings()` at all.
- **Honesty about scope**: a vector diff sees strokes, not meaning. Text changes need a separate word-level layer; raster content needs the existing `align.py` pixel diff. Say so in the report rather than implying completeness.

### A.2 Reuse map (do not rebuild these)

| Need | Existing engine |
|---|---|
| Segment extraction (l/re/qu/c, /Rotate fix via `page.rotation_matrix`, ≥`min_len_pt` filter, 0.5 pt direction-insensitive dedupe) | `extrude.extract_segments` — add a `max_segments` passthrough (the 4000 cap is too low for diffing dense sheets; default 20000 for diff) |
| Registration (dx, dy pt + rotation about page center, score 0..1) | `align.auto_align` |
| Raster fallback / text-and-raster completeness | `align.comparison_image` / `make_comparison_pdf` |
| Cloud outline geometry (scalloped polyline, y-down) | `markups.cloud_path_points` — reuse the point generator, stroke it through minipdf (do **not** go through the annot writer; the redline is a standalone page) |
| PDF output (deterministic bytes, dash, gray strokes, Helvetica labels) | `minipdf.Canvas` (`setDash`, `setStrokeColorRGB`, `line`, `drawString`) |
| \xa0 normalization for the word layer | the `core._normalize_text` policy (apply the same normalization to word text) |

### A.3 Coordinate + tolerance decisions

All work happens in **viewer page points, top-left origin, y down** (the markups/Fieldstitch convention; `extract_segments` already delivers this).

| Constant | Default | Rationale |
|---|---|---|
| `TOL_PT` (endpoint/interval quantum) | **0.5 pt** | matches `extrude._QUANT_PT`; 0.5 pt ≈ 0.007" paper ≈ 0.67" real at 1/8"=1'-0" — far below anything drawn deliberately, far above CAD-export float jitter (~1e-3 pt). Tolerance exists for *producer jitter and align-transform rounding*, not sloppy geometry |
| `THETA_TOL` | 0.15° | line-bucket angular quantum |
| `RHO_TOL` | 0.5 pt | line-bucket offset quantum (ρ measured from **page center**, not origin, to keep magnitudes small) |
| `GAP_TOL` | 0.75 pt | max gap bridged when merging collinear intervals into chains (kills the split/merge false diff; deliberately smaller than typical dash gaps ~3–6 pt so dashed linetypes stay dashed) |
| `MIN_DIFF_PT` | 2.0 pt | diff intervals shorter than this are rounding slivers — dropped |
| `CLUSTER_CELL` | 24 pt (1/3") | region clustering grid |
| `ALIGN_APPLY` | score ≥ 0.35 and (\|dx\|>0.4 or \|dy\|>0.4 or rot≠0) | below score 0.35, warn "sheets may not correspond" and diff unaligned |

### A.4 The algorithm — one machinery, no special cases

The whole diff is **1-D interval algebra per infinite line**. Exact matches, splits, merges, extensions, and partial erasures all fall out of the same code path.

```text
diff_page(base_pdf, rev_pdf, page_a, page_b, align=None):
  1  A = extract_segments(base_pdf, page_a, min_len_pt=1.5, max_segments=20000)
     B = extract_segments(rev_pdf,  page_b, ...)
  2  if align worth applying:                       # AlignResult from align.auto_align
        for p in B endpoints:                       # viewer pt, y down
            p' = ctr + R(θ)·(p − ctr) + (dx, dy)    # R(θ) = [[c,−s],[s,c]], θ = align.rotation
     (ctr = rev page center; same prerotation-then-shift order align.py renders with)
  3  LINE KEY per segment (both sets together):
        u = (b−a)/|b−a|;  if θ = atan2(uy,ux) < 0 or θ ≥ π: flip u   # θ ∈ [0, π)
        n = (−uy, ux);  ρ = n · (mid − page_ctr)
        cell = (floor(θ/THETA_TOL), floor(ρ/RHO_TOL))
  4  GROUP lines: union-find over occupied cells, joining each cell to its
     3×3 neighbors; ACROSS the θ = 0/π seam the direction flips, so the
     wrap-probe compares (θ ± π, −ρ).  Each group adopts the direction u* of
     its longest member (stability).
  5  PER GROUP, PER SIDE (old / new):
        project every endpoint: t = u* · (p − page_ctr)
        intervals = [(min t, max t)] per segment
        sort by t0; MERGE chains where gap ≤ GAP_TOL      # <- split/merge killer
  6  DIFF the two merged interval sets (sorted sweep):
        added   = NEW \ OLD;  removed = OLD \ NEW;  unchanged = OLD ∩ NEW
        drop pieces shorter than MIN_DIFF_PT
        reconstruct piece endpoints: p(t) = page_ctr + t·u* + ρ̄·n*
  7  WORD LAYER (text is invisible to get_drawings):
        words = page.get_text("words"), pushed through page.rotation_matrix
        (the sheets.py trap: words may arrive unrotated), text NFC/\xa0-normalized
        key = (text, round(x0/2), round(y0/2)); multiset diff -> added/removed words
  8  CLUSTER regions: every diff piece (segment or word bbox) marks the
     CLUSTER_CELL cells its bbox touches; union-find, 8-connected; region =
     member pieces, bbox = union of member bboxes + 6 pt margin.
     Sort regions by (added_len + removed_len) descending; number Δ1, Δ2, …
  9  RETURN DiffReport{regions:[{bbox, n_added, n_removed, added_len_pt,
     removed_len_pt, has_text_change}], totals, align_used, warnings}
```

Data structures: numpy `(N,4)` arrays for segments; plain dicts `cell -> [idx]` for both hash grids; a 40-line union-find. No spatial libraries needed.

**Why interval algebra and not segment pairing:** pairing (bipartite matching) must special-case one-old→two-new splits, two-old→one-new merges, and extensions; interval union per line makes them all identities. The prompt's classic false-diff case — a line split into two collinear pieces — merges back into the same chain in step 5 and diffs to nothing in step 6, *by construction*.

### A.5 Redline overlay rendering (minipdf, standalone page)

One page per compared pair, page size = base page rect. minipdf is **y-up**: emit every point as `(x, H − y)`.

| Element | Style |
|---|---|
| Unchanged linework | gray 0.78, width 0.4 (context, recedes) |
| Removed | **dashed** house red (0.84, 0.06, 0.06), dash `[3, 2]`, width 0.9 |
| Added | **solid** blue (0.118, 0.314, 0.784) — the exact `align.py` overlay-blue, so raster and vector compares speak one color language |
| Change-region clouds | `cloud_path_points(bbox, r=8)` polyline (y flipped), red, width 1.2, plus a bold `Δn` tag at the bbox corner (Helvetica-Bold 9) |
| Legend + summary | corner block: color key + "3 change regions — 41 added / 17 removed segments, 812 pt added / 305 pt removed; text changes in Δ2" + alignment note (dx/dy/rot/score) when applied |

Invariant #6 check: clouds are reserved *against the stamper output*; a compare artifact deliberately mimics addendum clouding — **get the owner's sign-off on the clouded proof sheet before shipping** (same protocol as invariant #2 style changes). minipdf output is deterministic (content-hash /ID, no metadata), so the PDF is byte-stable for tests. Write via `fsutil`'s atomic-write idiom.

GUI: one new button in `tab_compare` ("Vector diff PDF…", via the existing `run_bg`), enabled only when both slots are filled; report summary line in the status area; falls back with an honest message when a page has no vector linework (reuse `extract_segments`' ValueError text).

### A.6 Pitfalls (A)

- **Quantization boundary splits**: two coincident lines can land in adjacent (θ,ρ) cells. Cell-equality alone is wrong; the 3×3 union-find probe in step 4 is mandatory. Same for the θ=0/π **seam**: direction flips there, so ρ **negates** — probe `(θ±π, −ρ)` or horizontal-ish lines will randomly fail to group.
- **Align rotation sign**: `AlignResult.rotation` is a fitz prerotation (docstring: positive = CCW in PDF y-up = the plain `[[c,−s],[s,c]]` matrix in y-down viewer coords, applied about page center *before* the (dx,dy) shift). Do not derive this from reasoning alone — pin it with the rigid-transform fixture (A-5 below); a sign error diffs 100% of a rotated sheet.
- **Hatches and fills**: fill-only paths and hatch patterns explode into thousands of short segments, and a hatch re-tessellation shifts every one. `min_len_pt=1.5` + `MIN_DIFF_PT` + clustering keep the report readable, but a re-hatched area *is* a change region — report it, don't hide it; the summary should show pt-lengths so the reviewer sees "big region, small net length" and recognizes restyling.
- **Dash-phase churn**: a CAD dashed line exports as many short collinear segments; a phase shift between revisions produces equal-and-opposite added/removed slivers on one line. `GAP_TOL` must stay **below** dash gaps (bridging them would also bridge genuinely separate collinear walls); rely on `MIN_DIFF_PT` and note residual dash noise as a known limitation.
- **Bezier re-parameterization**: `extract_segments` chords the control polygon; the same visual curve with different control points diffs as changed. Acceptable for plan sets (curves ≈ door swings/fillets); document as a limitation rather than building curve canonicalization.
- **Text**: absent from `get_drawings`. Without the word layer (step 7), a changed dimension value reports "no change" — the single most damaging silent failure for a construction reviewer. The word layer is ~30 LOC; build it. Remember the `get_text("words")` unrotated-coords trap (CLAUDE.md gotcha) — transform through `rotation_matrix`, never bounds-check.
- **The 4000-segment cap** in `extract_segments` silently truncates dense sheets — truncation on one side manufactures diffs on the other. Raise via parameter for diff use and **warn loudly** if either side hits the cap.
- **Different page sizes** (re-plotted half-size, or cropped): detect rect mismatch > 1 pt and warn; do not attempt scale recovery in v1 (align.py has no scale term either).

### A.7 Acceptance (A) — deterministic, offline, fixtures built in-test with minipdf/fitz

1. **Exact-echo**: identical PDFs → 0 added, 0 removed, 0 regions.
2. **Counted edits**: base = line grid + 2 rects; rev removes 2 segments, adds 3, moves 1 (=1 removed + 1 added) → exactly `removed==3, added==4`, region count as constructed.
3. **Collinear split** (THE case): base `(100,100)-(300,100)`; rev = `(100,100)-(190,100)` + `(190,100)-(300,100)`, and a second variant with a 0.5 pt gap at the joint → **0 diffs** both ways. Mirror test for merge (two→one).
4. **Extension**: line lengthened 50 pt → one added interval, `abs(len−50) ≤ 1.0`, 0 removed.
5. **Rigid transform**: rev = base content translated (7.3, −4.1) pt and rotated 1.5° (built by transforming the draw calls); `auto_align` feeds the diff → 0 diffs. This test pins the rotation-sign convention.
6. **Region clustering**: edits in 3 far-apart corners → exactly 3 regions, Δ-ordering by magnitude.
7. **Word layer**: one dimension string changed → 1 region with `has_text_change`, 0 linework diffs.
8. **/Rotate 90** base page (smoke_test.py pattern) → same counts as rotation-0 twin.
9. **Renderer**: redline PDF byte-identical across two runs; page size == base rect; opens in fitz with expected page count.
10. **Honest failure**: raster-only page → ValueError message surfaced, no crash; cap-hit → warning present in report.

---

## B. CPM Scheduler (`rfi_stamper/cpm.py`)

### B.1 Industry norms

- The universal method is the **Precedence Diagram Method** (activity-on-node): forward pass (ES/EF), backward pass (LS/LF), Total Float, Free Float, critical chain = zero-total-float path. Every professional scheduling tool computes exactly this; differences are in constraint/calendar frills, all skippable.
- Link types: FS is ~90% of real construction logic. **v1 = FS + integer lag only** (SS/FF exist mostly in schedules built by full-time schedulers; adding them doubles the pass logic for little field value).
- **Workday math, not calendar days**: durations and floats are quoted in workdays; weekends are non-working by default. Holiday calendars are a per-project data-entry burden — v1 ships a weekend mask only.
- Display conventions reviewers expect: **critical bars red**; **total float as a trailing hollow bar** (EF→LF); a vertical **data-date line**; relationship arrows (optional toggle — useful but noisy).
- A professional implementation **refuses cycles loudly** (names the loop) instead of hanging or silently dropping links, and tolerates dirty data (dangling predecessor ids, bad dates) with warnings — `project.py` records are hand-edited JSON.

### B.2 Fit to the existing store (read `project.py` first — it's already there)

`ScheduleItem` already carries `start`, `end` (ISO, inclusive), `pct`, and **`depends: list of prerequisite ids`**. No schema migration needed:

- **Duration** = inclusive workday count of `[start, end]`, min 1. (Milestones = skip list.)
- **FS lag**: encode as an optional suffix in the depends entry — `"<id>+3"` / `"<id>-1"` (workdays). Backward compatible: a bare id is lag 0. Negative lag allowed (standard), documented.
- **Entered start acts as start-no-earlier-than** in the forward pass: `ES_i = max(pred-driven ES, to_index(item.start))`. One line of code; without it the computed schedule contradicts the user's own bars and the overlay looks broken. (Full constraint types stay in the skip list.)

### B.3 The math (exact formulas; integer day-indices, zero floats)

**Calendar layer.** Anchor `d₀` = earliest `start` in the set. With weekend mask `W ⊂ {0..6}` (default `{5,6}`):
`to_index(d)` = number of workdays strictly before `d`, counting from `d₀`; `from_index(k)` = the (k+1)-th workday on/after `d₀`. Both are simple loops (spans are years, not centuries — no closed-form cleverness needed). Convention: **ES/EF are morning indices** — an activity of duration `dur` starting index `s` occupies workdays `s … s+dur−1`; `EF = ES + dur`; its finish *date* = `from_index(EF − 1)`. Document this once; the off-by-one here is the classic CPM bug.

**Passes** (over the DAG; `lag` in workdays):

```text
Kahn topological sort; if any node unprocessed -> cycle:
    walk predecessors among the leftover nodes until a repeat -> report the
    actual loop by title: "schedule logic loops: Slab -> Frame -> Slab"
forward:   ES_i = max( max over preds p (EF_p + lag_pi),  to_index(start_i), 0 )
           EF_i = ES_i + dur_i
project    T = max over i (EF_i)
backward:  LF_i = min over succs s (LS_s − lag_is)   (no succs: LF_i = T)
           LS_i = LF_i − dur_i
floats:    TF_i = LS_i − ES_i            ( = LF_i − EF_i )
           FF_i = min over succs s (ES_s − lag_is) − EF_i   (no succs: T − EF_i)
critical:  TF_i == 0
```

Pure function: `analyze(items, weekend={5,6}, data_date=None) -> CpmResult` — per-id `{es, ef, ls, lf, tf, ff, dur, critical, es_date, ef_date, ls_date, lf_date}` plus `project_finish` date, `warnings` (bad dates, dangling depends — skipped with a message, never a crash, matching `_num`'s tolerance philosophy), or `cycle=[titles]` (whole analysis refused — honest, simple). Read-only: **never writes to the store**; an explicit future "reschedule" button is the only thing that should ever write dates back.

### B.4 GUI (surgical additions to `ScheduleView` in `gui/tab_field.py`)

- Compute `analyze(...)` **once in `refresh()`**, stash the result, and let `_draw_at(t)` only read it — the fx scheduler runs `_draw_at` per animation frame and per-frame CPM recompute violates the zero-idle-CPU house rule.
- Critical items: bar outline + fill tint in the theme error red (`c["err"]`), keeping the sweep-in animation untouched.
- Total float: thin **hollow** bar from the activity's end to `from_index(lf−1)`, outline `c["muted"]`; omit when TF = 0.
- The existing dashed TODAY line **is** the data-date line (relabel "DATA DATE" only if the owner wants; default `data_date=today`).
- Optional "Show logic" toggle: elbow polylines pred-bar-end → succ-bar-start with a small arrowhead; off by default.
- Cycle/warnings: one muted status line above the canvas ("logic loops: A → B → A — floats not computed"), never a modal.

### B.5 Pitfalls (B)

- **Off-by-one at every boundary**: inclusive end dates vs exclusive EF indices; one convention (B.3), one round-trip test (`from_index(to_index(d)) == d` for workdays).
- **Anchor on a weekend**: `to_index` defined as "workdays strictly before" works for any anchor date — do not require `d₀` to be a workday.
- **Weekend-only items** (`start`/`end` both non-workdays) → dur would be 0: clamp to 1 and warn.
- **Dangling `depends`** (removed items don't cascade in `project.remove`) and **junk dates** (hand-edited JSON): skip-with-warning, per record — one bad row must never sink `analyze` (mirror `ScheduleView`'s existing per-item `try/except ValueError`).
- **Negative lag** can pull `ES` before a predecessor's start — legal PDM, but clamp `ES ≥ 0` and let TF absorb it.
- Ties in `min`/`max` must be deterministic: iterate items in stored order, no set iteration.

### B.6 Acceptance (B) — the hand-computed textbook fixture

Network (durations in workdays, FS links, one lag):
A(3)—; B(4)—; C(2)←A; D(5)←A,B; E(4)←C **+1 lag**; F(3)←D; G(2)←E,F.

| Task | dur | ES | EF | LS | LF | TF | FF | critical |
|---|---|---|---|---|---|---|---|---|
| A | 3 | 0 | 3 | 1 | 4 | 1 | 0 | no |
| B | 4 | 0 | 4 | 0 | 4 | 0 | 0 | **yes** |
| C | 2 | 3 | 5 | 5 | 7 | 2 | 0 | no |
| D | 5 | 4 | 9 | 4 | 9 | 0 | 0 | **yes** |
| E | 4 | 6 | 10 | 8 | 12 | 2 | 2 | no |
| F | 3 | 9 | 12 | 9 | 12 | 0 | 0 | **yes** |
| G | 2 | 12 | 14 | 12 | 14 | 0 | 0 | **yes** |

Project = 14 workdays; critical path B→D→F→G. The fixture deliberately exercises: a merge point (D), a lag (C→E: TF 2 but **FF 0**, while E has FF 2 — the TF/FF distinction), and multiple terminal/initial nodes. Tests assert **every cell** of this table plus:

1. Calendar mapping: anchor Mon 2026-01-05 → A starts 2026-01-05, finishes Wed 2026-01-07; D starts Fri 2026-01-09, finishes Thu 2026-01-15 (crosses a weekend); `to_index`/`from_index` round-trip.
2. Two-node and three-node cycles → `cycle` names the loop members in order; no exception escapes.
3. Dangling pred id and a junk date → warning strings present, remaining items still analyzed.
4. Entered-start SNET: give C `start` = workday 5 → C's ES becomes 5, TF drops to 0 recompute (asserts the max() term).
5. Determinism: `analyze` twice → equal results.
6. GUI construct under xvfb (extend `test_gui_construct`): ScheduleView with a CPM-bearing project draws critical/float bars without error; no CPM call from inside `_draw_at`.

---

## Shared delivery notes

- Two new modules, no flags needed (nothing is being *replaced*, so the OCR_PLAN staged-parity pattern doesn't apply; ship behind the normal "new tab button" surface).
- Register both test files in `tests/run_all.py`; add the two repo-map lines to CLAUDE.md and the two naming-registry rows to HANDOFF.md **after** the owner blesses the names and the clouded redline proof sheet (invariant #2/#6 sign-off protocol).
- No vendor/product names anywhere: "the slip-sheet overlay compare familiar from drawing-review practice", "precedence-diagram scheduling as practiced across the industry".

## Open questions

1. Multi-sheet auto-pairing for the diff (match pages across two plan sets by sheet number via `sheets.py`) — v1 is single page-pair like `tab_compare`; is the batch pairing wanted in v1.1?
2. Clouds on the redline overlay need explicit owner sign-off against invariant #6 (they are addendum-style by intent, but the invariant reserves cloud shapes away from stamper output — confirm the compare artifact is exempt).
3. `extract_segments` cap: raise via parameter (proposed) or a diff-local re-extraction? Parameter keeps one extractor.
4. Lag syntax `"<id>+N"` inside `ScheduleItem.depends` vs a parallel `lags` field — suffix parsing is zero-migration but slightly magical; owner preference?
5. Should the CPM ever write computed dates back (an explicit "reschedule to logic" action), or stay analysis-only forever?
6. Default `TOL_PT=0.5`: confirm against the owner's actual CAD export chain (some producers re-tessellate curves/hatches aggressively; if so, bump `MIN_DIFF_PT` rather than the tolerance).


# Appendix H — OCR correction-review GUI + Tracer P5

# Track 8 — OCR Correction-Review GUI + Tracer P5 (touching-glyph residual)

## 0. Repo baseline (verified by reading the code and running the eval)

Running `python3 tests/test_tracer_eval.py` today (deterministic, offline) measures:

| Tier | CER | WER | Assert today |
|---|---|---|---|
| clean auto-labeled | **0.00%** | 0.00% | ≤ 2% |
| speckled photocopy | **0.00%** | 0.00% | ≤ 2% (thin-glyph guard) |
| touching-glyph photocopy | **3.38%** | **100.00%** | ≤ 15% loose ceiling, `> 0` |
| sheet-number field | 100% (raw 90% → index snap) | — | ≥ 99% |

Load-bearing facts about the current code:

- `tracer/lexicon.py` — `TAU_LO = 0.60`, `TAU_HI = 0.90`, `_LIFT_TO = 0.95`. `correct()` drops below τ_lo (`keep=False`), tags the mid-band with `why += "|low_conf"`, and marks every repair with a machine-readable `why` (`sheet:index_snap`, `word:lexicon_snap`, `dim:grammar_repair`) and `changed=True`. **The review-queue predicate already exists as data — nothing new to compute.** The `Lexicon` also already owns a char-3-gram table (`_grams`, `plausible()`), the seed for the P5 language prior.
- `tracer/profile.py` — `Corrections.record_correction(cell, char)` → pending list; `Corrections.promote(ensemble)` is **the only path** a human label reaches the kNN memory (never the MLP/NCC). `FontProfile.from_ensemble / save / load / apply_to` is a complete per-firm `.npz` sidecar. The GUI only needs to *drive* these, not extend them.
- `tracer/segment.py` — `split_glyph_boxes` = width trigger (`> 1.3× med_w`) → whole-glyph early-exit (`SPLIT_WHOLE_CONF = 0.82`) → `candidate_cuts` (pitch-snapped valleys + one descending drop-fall) → `dp_recombine`, which **sums raw positive confidences**. Segment score is classifier confidence only — no language prior, no merge candidates, and `group_words` runs *before* splitting (why touching-tier WER is 100%: a weld spanning an inter-word space stays one "word" forever).
- `tracer/__init__.py::read_image` — assembles per-word `cells / aspects / rel_ys / spans / ranked` then **discards them**; the review GUI needs a tap here.
- `gui/tab_pdftools.py::ocr()` — the one OCR entry point (`ocr.ocr_pdf` → `tracer.write_searchable`), runs under `widgets.run_bg` (worker thread; tk untouched off-thread).
- GUI raw materials: `widgets.make_tree` (ttk.Treeview), `widgets.run_bg`, `widgets.toast`, `tab_compare.np_to_photo` (numpy → PPM `P6` → `tk.PhotoImage`), the CLAUDE.md PhotoImage-reference gotcha, `fsutil` atomic writes, `~/.planloom` prefs dir.

---

## A. The correction-review GUI (human-in-the-loop)

### A1. Industry norms (what a professional verification station gets right)

Production OCR verification workflows converge on the same shape (confidence-routed review is the standard pattern in document-AI HITL pipelines; see sources at the end):

1. **Review only the uncertain band.** Auto-accept ≥ τ_hi, auto-drop < τ_lo, queue only τ_lo ≤ conf < τ_hi. Reviewers must never wade through confident reads — at hundreds of tokens per document, queue size is the whole UX.
2. **Plus: confirm silent auto-repairs.** Anything the corrector *changed* (index snap, lexicon snap, grammar repair) was lifted to 0.95 — above τ_hi — so a pure mid-band filter would hide exactly the tokens where the machine overrode the pixels. Professional stations surface machine corrections for one-keystroke confirmation.
3. **Image is ground truth.** Side-by-side glyph-crop image (zoomed) + editable text, always. The reviewer judges pixels, not the machine's text.
4. **Keyboard-first.** Accept = Enter, next/skip = Tab, previous = Shift+Tab, batch-accept = one chord. Reviewers do hundreds of items; a mouse round-trip per item kills throughput. Sub-second item-to-item latency.
5. **Corrections are training signal — but human-gated.** Every accepted edit becomes a labeled exemplar, *pending*, and nothing reaches the shipped model until an explicit promote action. **Never auto-train from unreviewed edits** (this is already `profile.py`'s contract; the GUI must not invent a bypass).
6. **Audit trail.** Who/when/what for every decision (accept / edit / reject / batch), append-only, local.

### A2. Build recipe

**Step 1 — engine tap (`tracer/__init__.py`, ~25 LOC).** Add `read_image(..., review_sink=None)`. When a list is passed, after the `lexicon.correct` call append one record per queue-worthy word:

```
ReviewItem = NamedTuple(
    page: int,            # filled by write_searchable
    bbox: (x0,y0,x1,y1),  # raster px, inclusive
    raw: str,             # pre-correction text
    text: str,            # post-correction text (editable default)
    conf: float, why: str,
    glyphs: [(cell28: np.float32(28,28), abs_bbox, char, conf)],
)
```

Queue predicate (exactly the norms above, from data that already exists):

```
queue = (TAU_LO <= conf < TAU_HI) or (res["changed"] and res["why"].split("|")[0]
         in {"sheet:index_snap", "word:lexicon_snap", "dim:grammar_repair"})
```

`review_sink=None` (default) must be **byte-identical** to today — same pattern as the P3 `ctx=None` no-op. Record `ng.cell` (the *normalized* cell the classifier saw), plus the raw crop bbox for display. Thread `review_sink` through `write_searchable` (stamping `page`) and `ocr_pdf`.

**Step 2 — overrides on the writer (`tracer/searchable.py`, ~15 LOC).** `write_searchable(..., overrides=None)` where `overrides = {(page, bbox): text}` replaces a word's text just before `insert_text`. After a review session the GUI re-runs `write_searchable` with the accepted texts — deterministic, and the existing pixel-diff verify step re-proves the raster untouched. (No in-place PDF surgery.)

**Step 3 — the deck (`gui/review_deck.py`, ~300 LOC).** A `tk.Toplevel` opened from `tab_pdftools` after an OCR run via a new button `Review uncertain reads (N)` (enabled when the sink is non-empty; sink captured in the `run_bg` worker, handed to the UI thread in `done`). Layout:

- **Top: virtual list** = `widgets.make_tree` Treeview, columns `(#, page, conf, raw → text, why, status)`. A Treeview holds thousands of rows cheaply because rows are *data*, not widgets — the "virtual list" answer for tk is: **never create a widget or a PhotoImage per row.** Only the selected row renders an image.
- **Middle: detail pane.** Left = the word crop from the page raster, integer-zoomed to ~96 px tall (`k = max(1, round(96/h))`, `np.repeat(np.repeat(crop,k,0),k,1)`), gray→RGB stack, then reuse `tab_compare.np_to_photo` (PPM `P6` header + bytes). Under it, the per-glyph strip: each `(cell, char, conf)` as a small zoomed cell image with its char and conf below, mid-band glyphs tinted. Right = `ttk.Entry` pre-filled with `text`, plus `raw`, `why`, `conf` labels.
- **PhotoImage lifetime:** keep every PhotoImage in a `self._photos` list for the visible item (CLAUDE.md gotcha — tk garbage-collects otherwise); a dict LRU of ~8 items is plenty.
- **Keys (bound on the Toplevel; Entry keeps focus):** `Return` = accept (commit Entry text, mark ✓, advance); `Tab` = skip/next (**must `return "break"`** or tk's focus traversal eats it); `Shift-Tab` = previous; `Escape` = close (confirm if undecided items remain); `Ctrl+Return` = **batch accept-all-above-threshold** — a Spinbox (default `TAU_HI` 0.90) + one confirm dialog; batch items are audit-tagged `"batch"`.
- **Correction routing on accept-with-edit:** uppercase the edit; if `len(edit) == len(glyph cells)` record `(cell, edit[i])` via `Corrections.record_correction` for every position where `edit[i] != char[i]` and `edit[i] in CHARSET`. **If lengths differ, record nothing to the glyph lane** — the cell↔char alignment is unknown (a segmentation error, not a label); the text still flows to overrides + audit. This asymmetry is the single most important correctness rule in the deck.
- **Promote is its own button:** `Promote N corrections to memory…` → confirm dialog → `Corrections.promote(ensemble)` → offer `Save as firm font profile…` → `FontProfile.from_ensemble(ens, producer).save(~/.planloom/fontprofiles/<name>.npz)`. Producer metadata is often stripped (the app's own NDA tooling does it) — fall back to a user-typed profile label. Accepting items must **never** implicitly promote.
- **Finish:** `Apply N accepted reads…` re-runs `write_searchable` with overrides under `run_bg`, toasts, re-enables `Open result`.

**Step 4 — audit trail (~40 LOC).** Append-only JSONL at `~/.planloom/tracer_reviews.jsonl`: `{ts, doc, page, bbox, raw, final, action: accept|edit|reject|batch|promote, conf, why}`. Written once per session close via `fsutil`'s atomic write (read-modify-write of the whole file; sessions are hundreds of lines, not gigabytes). No names beyond file paths — policy-clean.

**Step 5 — tests** (see acceptance).

### A3. Tk-specific pitfalls (part A)

- **PhotoImage GC** — keep Python refs per visible item or images blank mid-display (CLAUDE.md).
- **`Tab` traversal** — every key handler that owns Tab must `return "break"`.
- **Never touch tk from the worker** — the sink list is filled in the worker; the deck is constructed in `on_done` on the UI thread (`run_bg` contract).
- **Don't render per-row images** in the Treeview — thousands of PhotoImages is the classic tk OOM/latency trap; detail-pane-only rendering *is* the virtual list.
- **Cells vs crops** — promote the *normalized* `ng.cell` (what `add_exemplar` featurizes), display the raw crop. Promoting display crops would poison the kNN with un-normalized features that never match runtime.
- **`Corrections` alignment rule** — no glyph-lane recording when edit length ≠ cell count (see A2 step 3).
- **Fresh-ensemble trap** — `classify.default_ensemble()` may construct per call; the deck must promote into the *same* ensemble object the next OCR run will use, or persist via `FontProfile` and re-apply. Decide once: hold one ensemble on the deck/session and save a profile on promote (recommended — profile is the durable artifact).

---

## B. Tracer P5 — the touching-glyph residual

### B1. State of the art that fits pure numpy

The classical (pre-neural, CPU-cheap) architecture for touching machine print is **over-segment → recognize → recombine with a language model**, per the canonical segmentation survey literature (Casey & Lecolinet's strategy taxonomy: "dissection + recognition-based recombination"). Concretely:

1. **Cut-candidate generation**: vertical-projection valleys + pitch estimates, refined by **drop-fall** water paths — the standard family has four variants (descending-left/right, ascending-left/right); descending paths hug the top contour, ascending paths recover cuts that a top-seeded path misses when the weld is near the baseline. The repo already has one descending variant.
2. **Merge candidates for *broken* glyphs**: the lattice must also allow *joining* adjacent primitives (a snapped `E`, a two-piece `K`) — segments spanning 1..k consecutive connected components, not only sub-cuts of one component.
3. **Recombination as a lattice search**: DP/Viterbi over cut boundaries where a segment's score blends **classifier confidence with a character language model** — this is the standard fix over confidence-only DP, and it is exactly what OCR_PLAN §2.12 already prescribes: `score = α·logP_channel + (1−α)·logP_LM, α ≈ 0.6`.

All of this is small numpy + stdlib; no new deps.

### B2. Build recipe (algorithms + formulas)

**Step 1 — char bigram prior (`tracer/lexicon.py`, ~30 LOC).** Build once per `Lexicon` from `self.words` plus the sheet-shape alphabet and dimension-grammar strings, with `^`/`$` anchors and add-k smoothing (OCR_PLAN §5 n-gram row):

```
P(c | c') = (count(c'c) + k) / (count(c') + k·|CHARSET|),  k = 0.01
LB[c', c] = ln P(c | c')          # 45×45 float32, cached like _grams
```

Expose `lexicon.bigram_lp()` (module-level default when no Context, so `read_image` without P3 hooks still gets the prior — it is built from the *builtin* word list, no client data).

**Step 2 — unify split + merge in one word-level lattice (`tracer/segment.py`, ~90 LOC, replaces `dp_recombine` internals).** Per word (after `merge_broken`):

- Primitives = the word's boxes left-to-right; every box wider than `TOUCH_WIDE_FACTOR × med_w` that fails the `SPLIT_WHOLE_CONF = 0.82` whole-glyph early-exit contributes its `candidate_cuts` as internal boundaries. Boundary list `B` = box edges + internal cuts, sorted.
- A lattice segment `[B[i], B[j])` is admissible when its width ∈ `[SEG_W_LO, SEG_W_HI] × med_w` **and** any gap it spans between primitives is ≤ `0.25 × med_w` (that's the broken-glyph **merge** move; a bigger gap is a real space and never merged).
- **Batch, then search:** collect every admissible segment's crop, `norm_glyph` all of them, and make **one** `clf.classify_batch` call per word (the repo's matmul-batching pattern). Per-segment `classify()` inside the DP loop is the O(B²)-model-calls trap.
- Viterbi over `(boundary j, last char c)` — 43 states per boundary, top-3 chars per segment from the ranked output:

```
best[j][c] = max over i<j, c' :
    best[i][c'] + α·ln p_clf(c | seg(i,j)) + (1−α)·LB[c', c]
α = 0.6;  start uses LB['^', c];  add LB[c, '$'] at the final boundary.
Ties broken by (lower i, lexicographic c) — determinism.
Fallback: if no admissible path, return the whole box (today's behavior).
```

- **Fix the positive-sum bias while you're in there:** today's `dp_recombine` sums raw positive confidences, so more segments monotonically score higher and only the width floor restrains over-splitting. Log-domain scoring removes that bias direction (more segments = more negative terms); keep a small tunable per-segment offset κ (start 0.0, sweep ±0.05 against the eval) and keep the `SPLIT_WHOLE_CONF` early-exit so wide single glyphs (`M W 0`) are never shredded — that guard is what protects the clean tier's 0.00%.

**Step 3 — ascending drop-fall (`tracer/segment.py`, ~15 LOC).** Mirror `_dropfall_cut` bottom→top ({up, up-left, up-right}, least ink); `candidate_cuts` adds both refined columns per seed valley. Also add a **neck test** for valley admission: a genuine weld neck carries ≈ one stroke of ink, so admit a local minimum when `prof[c] ≤ 1.5 × stroke_w` where `stroke_w` = median horizontal ink-run length inside the crop (one pass over the crop's runs) — the current `0.6 × prof.mean()` rule under-generates cuts on short two-glyph crops where the mean is dominated by the glyphs themselves. Cap total cuts at `2·round(W/med_w) + 2` to bound the lattice.

**Step 4 — re-tokenize words after splitting (`tracer/__init__.py`, ~25 LOC).** This is the measured 100%-WER bug: `group_words` runs *before* `split_glyph_boxes`, so a weld across an inter-word space stays one word no matter how well the chars read. After the lattice yields final glyph spans for a "word", re-apply Wong's rule over the spans (`gap > WORD_GAP_FACTOR × med_h` → new word) and emit one tuple per re-opened word. Cheap, high-value: touching-tier WER should collapse from 100% to tens of percent.

**Step 5 — eval tiers (`tests/test_tracer_eval.py`, ~40 LOC).** See acceptance below, including a new harder "gen-3" fixture (two `synth._morph` dilation passes + blur 0.9 + noise 10) so the suite keeps an honest, non-zero residual to track after P5 lands.

### B3. The honest ceiling (what CER is realistic)

- The university-run **Annual Tests of OCR Accuracy (1992–1996)** are still the reference for degraded machine print: top commercial page readers of that era scored ~97–99% character accuracy on clean corpora but dropped by several points — into the ~90–98% band, i.e. **CER 2–10%** — on the degraded/photocopied document groups, and moving 300→200 dpi alone raised error counts ~50% ([Fourth Annual Test](https://www.semanticscholar.org/paper/The-Fourth-Annual-Test-of-OCR-Accuracy-Rice-Jenkins/fae039cc89b2cd453acb85d208e021907528b062), [Fifth Annual Test](https://www.expervision.com/wp-content/uploads/2012/12/1996.The_Fifth_Annual_Test_of_OCR_Accuracy.pdf), [Third Annual Test](https://www.researchgate.net/publication/244514570_The_Third_Annual_Test_of_OCR_Accuracy)).
- For a from-scratch engine on **real 3rd-generation photocopies** (touching + broken + partially sub-legible, all at once), a realistic end-state is **CER 3–10% raw**, pulled to ~1–3% on *structured fields* by the index/grammar snaps — which is why the confidence routing to part (A)'s review queue, not raw CER, is the deliverable that makes degraded sets shippable.
- The repo's *synthetic* touching tier (one dilation weld, no other damage) measures **3.38% today**; with the LM-lattice + merge + ascending drop-fall, **≤ 2.0% is an honest hard target** on that fixture. 1.5% is a stretch to *report toward*, not to assert — some welds are genuinely ambiguous even to the LM (e.g. a welded `LI` vs `U` in a non-word token has no channel or prior evidence either way), and pinning the assert at the stretch number is how flaky suites are born.
- The synthetic tier is a **lower bound, not a promise** — say so in the plan doc and keep OCR_PLAN §8's framing: real gen-3 paper also carries the sub-legible and fused-with-linework failure modes that stay SKIPped and confidence-routed.

### B4. Pitfalls (part B)

- **Positive-confidence-sum bias** in today's `dp_recombine` (over-split pressure restrained only by the width floor) — fix via log-domain, but then watch the opposite bias and keep the whole-box fallback path.
- **Clean-set regression is the real risk.** The `SPLIT_WHOLE_CONF` early-exit and the `SEG_W_LO/HI` width band are what keep `M W 0` whole; any lattice change must keep both, and the clean tier must stay **exactly 0.00%**.
- **LM overreach on non-words**: sheet tokens and dimensions must not be bent toward English-like bigrams — the prior is built *including* the sheet/dim alphabets, α stays at 0.6, and the P3 field grammars + number-lock remain the final arbiter downstream (the lattice never sees them; it only orders cuts).
- **Merge across real spaces**: the `0.25 × med_w` gap cap is the fence; without it the merge move re-welds words the scanner didn't.
- **Determinism**: fixed tie-breaks in the Viterbi; `np.argsort` on floats is stable given the tie rule, and the eval asserts identical output across two runs.
- **Per-segment classify calls**: batch all admissible segments per word into one `classify_batch`; the naive version regresses the OCR_PLAN §5 timing budget badly on dense pages.
- **`rel_y` for lattice segments** must be recomputed from each segment's own ink rows (as `split_glyph_boxes` does now) — reusing the parent box's band position mis-disambiguates marks (`-` vs `.` vs `'`).

---

## Acceptance criteria (all deterministic, all offline)

**Part A**
1. **Construct test** (extend `tests/test_gui_construct.py` or new `tests/test_review_gui.py`, xvfb): build the deck on a synthetic 3-item queue (tokens rendered with fitz exactly like `test_tracer_eval._render_token`); assert tree rows, detail pane populates, and — calling the handlers directly, never synthesizing OS key events (repo rule) — accept advances + marks, Tab skips, batch-accept at 0.90 accepts exactly the items with conf ≥ 0.90.
2. **Promote round-trip**: record a correction (rendered `5` labeled as `S`), assert ensemble store size unchanged (nothing trains from unreviewed edits), `Corrections.promote(ens)` returns 1 and store grows by 1; `FontProfile.from_ensemble → save → load → apply_to` a fresh ensemble adds the same exemplar; classifying the same cell now ranks the corrected char strictly higher than before.
3. **No-op guarantee**: `read_image(gray)` with and without `review_sink=[]` returns identical word tuples; the sink contains only mid-band (`TAU_LO ≤ conf < TAU_HI`) and `changed` items.
4. **Alignment fence**: an accept whose edit length ≠ glyph count files zero glyph corrections but still lands in overrides + audit.
5. **Overrides**: `write_searchable(..., overrides=...)` output contains the override text in `get_text` and the page raster pixel-diff still verifies clean.
6. **Audit**: session close writes parseable JSONL with one record per decision, atomically.

**Part B**
1. `tests/test_tracer_eval.py`: clean tier CER **== 0.00** (tightened from ≤ 2% — it is deterministic and currently 0.00); speckle guard unchanged (≤ 2%); existing touching fixture tightened **15% → ≤ 2.0%** (from measured 3.38%); **new gen-3 fixture** (double dilation) asserted `≤ 8%` and `> 0` — it inherits the "genuinely degrades" role so the suite always tracks a real residual.
2. Touching-tier **WER < 50%** (from measured 100%) — the word re-tokenization assert.
3. Sheet-number field accuracy ≥ 99% must hold unchanged.
4. Unit tests in `tests/test_tracer.py`-style: (a) a welded two-glyph crop where the LM breaks the confidence tie the right way; (b) a broken glyph split into two non-x-overlapping pieces rejoined by the merge move; (c) determinism — two runs of `read_image` on the touching fixture are identical.
5. Full `tests/run_all.py` green.

---

## SKIP list (do NOT build)

- Handwriting / hand lettering; sub-15 px text (flag & refuse stands, OCR_PLAN §8).
- Text fused with linework / hatch — still the honest §8 SKIP; the lattice does not attempt it.
- Full word-lexicon-constrained beam/trie decoding — the 43-class bigram prior + P3 downstream grammars capture nearly all the win at a fraction of the code.
- Any re-segmentation UI in the deck (drawing boxes, moving cuts) — text-edit only; misaligned edits simply don't train.
- Per-row thumbnails / image gallery in the queue list — detail-pane-only rendering.
- Auto-promotion, background retraining, MLP/NCC updates from corrections — kNN memory only, human-gated (the `profile.py` contract).
- Undo/redo stack, multi-document review sessions, reviewer accounts — one run, one queue, an append-only audit.
- Confidence-recalibration UI; any change to τ_lo/τ_hi from the deck.
- Editing the *input* PDF — overrides re-run the searchable writer; originals are never mutated.

## LOC estimate

- **A**: engine tap + overrides ~90; `gui/review_deck.py` ~300; `tab_pdftools` wiring ~25; audit ~40; tests ~170 → **~625**
- **B**: bigram prior ~30; lattice DP + ascending drop-fall + neck test ~120; word re-tokenization ~25; eval fixture/assert changes ~40; unit tests ~60 → **~275**
- **Total ≈ 900 LOC** (≈ 650 product, ≈ 250 test)

## Open questions (for the owner)

1. Should accepted reviews rewrite the searchable output (overrides re-run — recommended) or only feed learning + audit?
2. Where does the deck live: post-OCR Toplevel from PDF Tools (recommended, minimal), or a Ground Truth-section panel?
3. Profile naming when producer metadata is empty/stripped (the app's own NDA tooling strips it): user-typed firm label OK?
4. Touching-tier bar: assert ≤ 2.0% and report toward 1.5% (recommended), or hold a ≤ 3% assert with 2% report-only?
5. Should the `< TAU_LO` dropped reads appear as a view-only "rejected" tray in the deck, or stay invisible (recommended: a count in the header only)?
6. Is a resumable half-finished queue (JSON sidecar) in scope, or is a session strictly run-to-close?

**Sources:** [The Fourth Annual Test of OCR Accuracy](https://www.semanticscholar.org/paper/The-Fourth-Annual-Test-of-OCR-Accuracy-Rice-Jenkins/fae039cc89b2cd453acb85d208e021907528b062) · [The Fifth Annual Test of OCR Accuracy](https://www.expervision.com/wp-content/uploads/2012/12/1996.The_Fifth_Annual_Test_of_OCR_Accuracy.pdf) · [The Third Annual Test of OCR Accuracy](https://www.researchgate.net/publication/244514570_The_Third_Annual_Test_of_OCR_Accuracy) · [drop-fall touching-character segmentation](https://www.researchgate.net/publication/251947159_A_new_drop-falling_algorithms_segmentation_touching_character) · [improved drop-fall variants](https://ieeexplore.ieee.org/document/7746350/) · [HITL confidence-routed review patterns](https://oneuptime.com/blog/post/2026-02-17-how-to-set-up-human-review-for-document-ai-processing-results/view)
