"""The Batterboard run: nine stages, one result.

    Intake -> Sightline -> Read -> Cross-check -> Register -> Points ->
    Plates -> Self-check -> Deliver

Everything is built in a staging folder.  Only when the self-check gate
holds is it moved into ``<out>/deliverables``; a BLOCKED run writes
nothing there (the pattern of ``pipeline.py`` + ``verify.py``).  The run
log (``<out>/run_log``) always gets the report, pass or fail, so a
blocked run still says exactly what stopped it.

Result: ``PASS``, ``PASS WITH FLAGS`` (open items exist, or a level could
not be laid out and says why) or ``BLOCKED`` (a self-check failed, or
nothing could be read at all).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field

from .. import papertrail as pt
from .. import redtags
from . import crosscheck, intake, planread, plates, points, report, selfcheck, surveyor
from . import model as M
from . import notes as notes_mod
from .names import Names

RESULT_PASS, RESULT_FLAGS, RESULT_BLOCKED = "PASS", "PASS WITH FLAGS", "BLOCKED"


@dataclass
class RunResult:
    status: str = RESULT_BLOCKED
    stages: list = field(default_factory=list)
    out_root: str = ""
    deliverables: str | None = None
    log_dir: str = ""
    files: list = field(default_factory=list)       # delivered files
    opts: M.Options | None = None
    rr: object = None
    reg: object = None
    trail: object = None
    ctx: object = None
    points: dict = field(default_factory=dict)
    plate_manifest: dict | None = None
    manifests: list = field(default_factory=list)
    checks: list = field(default_factory=list)
    surveyor: list = field(default_factory=list)
    note_statements: list = field(default_factory=list)
    report_lines: list = field(default_factory=list)
    #: where THIS run's report went ("" when it could not be written).
    #: Usually run_log/run_report.txt and .pdf; a numbered name when that
    #: file was held open by another program.
    report_txt: str = ""
    report_pdf: str = ""

    @property
    def blocked(self) -> bool:
        return self.status == RESULT_BLOCKED

    def stage(self, name: str):
        for s in self.stages:
            if s.name == name:
                return s
        return None


def _sidecars(out_root: str, opts: M.Options) -> tuple:
    """Where the Red Tags and names registers live: beside the project
    file when the project settings name one, else in the output root."""
    proj = (opts.project or {}).get("path")
    if proj:
        base = os.path.splitext(proj)[0]
        return base + ".redtags.json", base + ".batterboard-names.json"
    return (os.path.join(out_root, "batterboard.redtags.json"),
            os.path.join(out_root, "batterboard.names.json"))


_REPORT_NAME = re.compile(r"run_report(_\d+)?\.(txt|pdf)(\.part)?$")


def _write_report(write, lines, folder: str, ext: str, log) -> str:
    """Write the run report; return the path it went to ("" when it could
    not be written at all).  The usual name is ``run_report<ext>``.  When
    that file is held open by another program the report goes beside it
    under a numbered name, so an earlier run's report is never passed off
    as this run's."""
    err = None
    for n in range(1, 10):
        path = os.path.join(folder, "run_report" + ("" if n == 1 else f"_{n}") + ext)
        try:
            write(lines, path)
            return path
        except OSError as e:           # in use by another program, or not writable
            err = e
            try:
                os.remove(path + ".part")
            except OSError:
                pass
        except Exception as e:   # noqa: BLE001 -- a report that could not be drawn
            log(f"  the {ext[1:]} report could not be written: {e}")
            return ""
    log(f"  the {ext[1:]} report could not be written: {err}")
    return ""


def run(paths, out_root: str, opts: M.Options | None = None, log=print,
        progress=lambda name, status: None) -> RunResult:
    """Run the whole package over a drop."""
    opts = opts or M.Options()
    res = RunResult(out_root=out_root, opts=opts)
    os.makedirs(out_root, exist_ok=True)
    res.log_dir = os.path.join(out_root, "run_log")
    os.makedirs(res.log_dir, exist_ok=True)
    stages = {n: M.StageResult(name=n) for n in M.STAGES}
    res.stages = [stages[n] for n in M.STAGES]
    staging = tempfile.mkdtemp(prefix="staging_", dir=res.log_dir)

    def done(name):
        progress(name, stages[name].status)
        log(f"[{stages[name].status.upper():5s}] {name}")

    def finish(status):
        res.status = status
        for n in M.STAGES:
            if n not in seen:
                stages[n].status = M.NOT_REACHED
                progress(n, M.NOT_REACHED)
        res.report_lines = report.build_lines(res)
        # earlier runs' reports go first; one that is open in another
        # program stays, and this run's report then takes a numbered name
        for name in os.listdir(res.log_dir):
            if _REPORT_NAME.match(name):
                try:
                    os.remove(os.path.join(res.log_dir, name))
                except OSError:
                    pass
        res.report_txt = _write_report(report.write_text, res.report_lines, res.log_dir,
                                       ".txt", log)
        res.report_pdf = _write_report(report.write_pdf, res.report_lines, res.log_dir,
                                       ".pdf", log)
        shutil.rmtree(staging, ignore_errors=True)
        log(f"RESULT: {status}")
        return res

    seen = set()
    reg_path, names_path = _sidecars(out_root, opts)
    try:
        names = Names(names_path)
        reg = redtags.Register(reg_path)
    except ValueError as e:
        stages["Intake"].block(str(e))
        seen.add("Intake")
        done("Intake")
        return finish(RESULT_BLOCKED)
    res.reg = reg
    trail = res.trail = pt.Trail()

    # ---- Intake ------------------------------------------------------------- #
    st = stages["Intake"]
    seen.add("Intake")
    unknown_opts = getattr(opts, "_unknown", [])
    if unknown_opts:
        st.block("the options file has settings this build does not know: "
                 + ", ".join(unknown_opts))
        done("Intake")
        return finish(RESULT_BLOCKED)
    hats = list(opts.hats or [])
    bad_hats = [h for h in hats if h not in M.HATS]
    if not hats or bad_hats:
        st.block("pick a hat: the Concrete hat, the Surveyor hat or both"
                 + (" (not a hat: " + ", ".join(str(h) for h in bad_hats) + ")"
                    if bad_hats else ""))
        done("Intake")
        return finish(RESULT_BLOCKED)
    concrete_hat = "concrete" in hats
    refs = intake.classify_drop(paths, opts)
    kinds = {}
    for r in refs:
        kinds[r.cls] = kinds.get(r.cls, 0) + 1
    st.say(", ".join(f"{n} {k}" for k, n in sorted(kinds.items())))
    for r in refs:
        if r.cls == M.PG_UNKNOWN:
            st.flag(f"not classified, not used: {r.name}"
                    + (f" page {r.page}" if r.page else "") + f" ({r.why})")
        elif r.cls in (M.PG_STRUCT, M.PG_ARCH, M.PG_SHOP, M.PG_CAD) and not r.level:
            st.flag(f"{r.sheet or r.name}: {r.cls} with no level read; assign its level to use it")
    if not any(r.cls == M.PG_STRUCT and r.level and not r.prior for r in refs):
        st.block("no structural plan with a level was found in the drop: there is nothing "
                 "to lay out")
        res.rr = planread.ReadResult(refs=list(refs),
                                     unknown=[r for r in refs if r.cls == M.PG_UNKNOWN])
        done("Intake")
        return finish(RESULT_BLOCKED)
    done("Intake")

    # ---- Sightline + Read ------------------------------------------------------ #
    rr = res.rr = planread.read_drop(refs, opts, names, log=log)
    st = stages["Sightline"]
    seen.add("Sightline")
    for s in rr.sight:
        c = s["record"].counts()
        st.say(f"{s['sheet']}: {c['judged']} judged, {c['dropped']} dropped, {c['clipped']} "
               f"replaced by the visible part, {c['obscured']} obscured, {c['unreadable']} "
               "not judged")
        if c["dropped"] or c["unreadable"] or c["unread_regions"]:
            st.flag(f"{s['sheet']}: hidden or unread geometry is listed in the report")
        with open(os.path.join(res.log_dir, f"sightline_{s['sheet'] or s['page']}.json"), "w",
                  encoding="utf-8") as fh:
            json.dump(s["record"].to_dict(), fh, indent=1)
    done("Sightline")
    st = stages["Read"]
    seen.add("Read")
    for lv in rr.levels:
        if lv.blocked:
            for why in lv.blocked:
                st.flag(f"{lv.name} not laid out: {why}")
        else:
            st.say(f"{lv.name}: {len(lv.elements)} elements at {lv.scale_label}")
    for k, a in enumerate(rr.asks):
        for j, c in enumerate(a["candidates"]):
            for m, png in enumerate(c.get("crops", [])):
                p = os.path.join(res.log_dir, f"ask_{a['sheet']}_class{j + 1}_{m + 1}.png")
                with open(p, "wb") as fh:
                    fh.write(png)
    for r in rr.remarks:
        st.say(r)
    live = [lv for lv in rr.levels if not lv.blocked]
    if not live:
        st.block("no level could be read; see the reasons above")
        done("Read")
        return finish(RESULT_BLOCKED)
    done("Read")

    # ---- Cross-check + Register --------------------------------------------------- #
    reg.begin_run(f"run-{len(reg.refs())}")
    ctx = res.ctx = crosscheck.run_checks(rr, trail, reg, opts, names, log=log)
    reg.end_run()
    st = stages["Cross-check"]
    seen.add("Cross-check")
    for lv in live:
        n_checks = sum(1 for (c, code) in ctx.proofs if code == lv.code and "/" not in c)
        n_skip = sum(1 for c in ctx.skips if c[1] == lv.code)
        st.say(f"{lv.name}: {n_checks} checks recorded, {n_skip} not compared (reasons in "
               "the report)")
    done("Cross-check")
    st = stages["Register"]
    seen.add("Register")
    total = reg.counts()
    st.say(f"{total.get('total', 0)} open item(s): {total.get('answer', 0)} answer needed "
           f"before layout, {total.get('confirm', 0)} confirm before forming, "
           f"{total.get('answered', 0)} answered")
    if total.get("answer", 0) or total.get("confirm", 0):
        st.flag("open items exist")
    for p in getattr(ctx, "answer_problems", []):
        st.flag(p)
    # notes that govern layout: each an exact quote with its proof record
    skip = set(opts.skip_notes or [])
    for n in notes_mod.governing(rr.notes):
        if f"{n.sheet}/{n.number}" in skip or n.ref in skip:
            continue
        p = trail.add("NOTE", quotes=[n.quote()], values={"sheet": n.sheet}, result="info")
        res.note_statements.append(trail.say(p.id).text)
    done("Register")

    # ---- Points ---------------------------------------------------------------------- #
    st = stages["Points"]
    seen.add("Points")
    allp = res.points = points.build_points(rr, reg, opts, names)
    staged = []
    for lv in live:
        lp = allp[lv.code]
        st.say(f"{lv.name}: {len(lp.rows)} points; {len(lp.withheld)} end(s) not laid out")
        if opts.out_kits:
            staged += points.export_level(lp, lv, os.path.join(staging, "points", lv.code), opts)
        elif lp.rows:
            # the self-check still needs a file to round-trip
            staged += points.export_level(lp, lv, os.path.join(staging, "_check", lv.code), opts)
    done("Points")

    # ---- Plates ------------------------------------------------------------------------- #
    st = stages["Plates"]
    seen.add("Plates")
    sv_results: dict = {}
    extra = None
    if "surveyor" in opts.hats:
        extra = surveyor.plates_extra(rr, reg, trail, allp, names, opts, sv_results)
    os.makedirs(os.path.join(staging, "plates"), exist_ok=True)
    proof_only = bool(opts.proof_only)
    plate_name = "Batterboard_one_sheet_proof.pdf" if proof_only else "Batterboard_plates.pdf"
    # With the plates output switched off the set is still built, in the
    # check folder: the picture check is the gate for the points too, so
    # it runs either way.  Only the delivery is skipped.
    deliver_plates = bool(opts.out_plates) or proof_only
    plate_dir = os.path.join(staging, "plates") if deliver_plates \
        else os.path.join(staging, "_check", "plates")
    os.makedirs(plate_dir, exist_ok=True)
    plate_pdf = os.path.join(plate_dir, plate_name)
    man = res.plate_manifest = plates.build(plate_pdf, rr, reg, trail, ctx, allp, opts,
                                            proof_only=proof_only, extra=extra)
    man["file"] = plate_pdf
    man["delivered"] = deliver_plates
    res.manifests = [man]
    staged.append(plate_pdf)
    n_list = sum(p["notes"]["list_only"] + p["tags"]["list_only"] for p in man["pages"])
    st.say(f"{len(man['pages'])} plate(s); {n_list} row(s) in the list only (no free spot "
           "within reach)")
    if not deliver_plates:
        st.say("the plates output is switched off: the plates were built for the "
               "self-check and are not delivered")
    if not opts.out_legend and not proof_only:
        st.say("the legend output is switched off: the LEGEND plate was not built")
    if not concrete_hat and not proof_only:
        st.say("the Concrete hat is off: " + M.CONCRETE_HAT_ONLY)
    extra_texts = []
    if not proof_only:
        # the phone booklet and the RFI drafts are open-item outputs of the
        # Concrete hat
        if opts.out_booklet and concrete_hat:
            bpath = os.path.join(staging, "plates", "Batterboard_phone_booklet.pdf")
            bman = plates.build_booklet(bpath, rr, reg, opts)
            if bman is not None:
                bman["file"] = bpath
                res.manifests.append(bman)
                staged.append(bpath)
        if opts.out_rfi_drafts and concrete_hat:
            ddir = os.path.join(staging, "open_items", "rfi_drafts")
            for t in reg.items():
                if not t.key.startswith("XC-ARCH-ENDS"):
                    continue
                text = reg.rfi_draft(t, project=opts.project)
                if not text:
                    continue
                os.makedirs(ddir, exist_ok=True)
                p = os.path.join(ddir, f"RFI_draft_{t.ref}.txt")
                with open(p, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(text)
                staged.append(p)
                extra_texts.append(text)
        os.makedirs(os.path.join(staging, "open_items"), exist_ok=True)
        ipath = os.path.join(staging, "open_items", "open_items.csv")
        report.items_csv(reg, rr, ipath)
        staged.append(ipath)
        if "surveyor" in opts.hats:
            fdir = os.path.join(staging, "forms")
            os.makedirs(fdir, exist_ok=True)
            cpath = os.path.join(fdir, "requested_control.csv")
            n_ctl = surveyor.control_csv(sv_results, rr, cpath)
            staged.append(cpath)
            for lv in live:
                r = sv_results.get(lv.code, {})
                plan = r.get("plan")
                res.surveyor.append(
                    f"{lv.name}: {len(r.get('controls', []))} requested control point(s)"
                    + (f"; {len(plan['setups'])} setup(s) serve {plan['served']} of "
                       f"{plan['total']} layout points" if plan else ""))
                if plan and plan["unserved"]:
                    res.surveyor.append(
                        f"  {len(plan['unserved'])} point(s) have no valid setup and are "
                        "flagged: " + ", ".join(x.name for x in plan["unserved"][:20]))
                    st.flag(f"{lv.name}: {len(plan['unserved'])} point(s) with no valid setup")
                lpath = os.path.join(fdir, f"as_staked_ledger_{lv.code}.pdf")
                if surveyor.ledger(lpath, allp.get(lv.code), lv, opts):
                    staged.append(lpath)
            spath = os.path.join(fdir, "slab_elevation_survey.pdf")
            sman = surveyor.survey_form(spath, rr, trail, opts)
            if sman is not None:
                sman["file"] = spath
                res.manifests.append(sman)
                staged.append(spath)
                res.surveyor.append("A slab elevation survey form was made: " + sman["quote"])
            else:
                res.surveyor.append("A slab elevation survey form was not made: the general "
                                    "notes in the drop do not call for one.")
            res.surveyor.append(f"{n_ctl} requested control point(s) written to "
                                "requested_control.csv.")
    done("Plates")

    # ---- Self-check ------------------------------------------------------------------------ #
    st = stages["Self-check"]
    seen.add("Self-check")
    lint_files = [f for f in staged if "as_staked_ledger" not in os.path.basename(f)]
    quotes = [q.text for p in trail.proofs() for q in p.quotes]
    res.checks = selfcheck.run_all(
        rr, reg, trail, allp, res.manifests, lint_files, opts,
        plate_pdf=plate_pdf, plate_manifest=man, extra_texts=extra_texts, quotes=quotes)
    # the report itself is an output: lint it before anything is delivered
    res.status = RESULT_FLAGS
    draft = report.to_text(report.build_lines(res))
    hits = pt.lint_text(draft, quotes=quotes + [t.wrong for t in reg.items()])
    res.checks.append({"check": "the run report passes the text lint", "ok": not hits,
                       "problems": [f"{h.kind} ({h.token})" for h in hits[:8]],
                       "detail": "", "counts": {}})
    failed = [c for c in res.checks if not c["ok"]]
    for c in res.checks:
        if c.get("skipped"):
            st.say(f"not run: {c['check']} ({c['skipped']})")
        else:
            st.say(f"{'held' if c['ok'] else 'FAILED'}: {c['check']}")
    if failed:
        for c in failed:
            for p in c["problems"][:6]:
                st.block(f"{c['check']}: {p}")
        done("Self-check")
        stages["Deliver"].status = M.NOT_REACHED
        stages["Deliver"].say("nothing was written to the deliverables folder")
        seen.add("Deliver")
        progress("Deliver", M.NOT_REACHED)
        return finish(RESULT_BLOCKED)
    done("Self-check")

    # ---- Deliver ------------------------------------------------------------------------------ #
    st = stages["Deliver"]
    seen.add("Deliver")
    deliver = os.path.join(out_root, "deliverables")
    prev = os.path.join(res.log_dir, "previous_deliverables")
    had_earlier = os.path.isdir(deliver)
    moved_aside = False
    delivered = []
    try:
        if had_earlier:
            # ONE rename: the earlier deliverables move aside whole, or not at
            # all.  (A copy-then-delete fallback empties half the folder and
            # then stops when one file in it is open in another program.)
            shutil.rmtree(prev, ignore_errors=True)
            os.rename(deliver, prev)
            moved_aside = True
        os.makedirs(deliver, exist_ok=True)
        for f in staged:
            rel = os.path.relpath(f, staging)
            if rel.startswith("_check"):
                continue
            dst = os.path.join(deliver, rel)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.move(f, dst)
            delivered.append(dst)
    except OSError as e:
        # a run that could not deliver leaves the folder as it found it
        if moved_aside or not had_earlier:
            shutil.rmtree(deliver, ignore_errors=True)
        if moved_aside:
            try:
                os.rename(prev, deliver)
            except OSError:
                pass
        kept = ("the earlier deliverables are as they were" if os.path.isdir(deliver)
                else "the earlier deliverables are in run_log/previous_deliverables"
                if had_earlier else "the deliverables folder was not made")
        st.block("the deliverables folder could not be written: a file in it is in use by "
                 f"another program, or the folder cannot be changed ({e}). Nothing was "
                 f"delivered; {kept}")
        done("Deliver")
        return finish(RESULT_BLOCKED)
    for lp in allp.values():
        lp.files = [os.path.join(deliver, os.path.relpath(f, staging)) for f in lp.files
                    if not os.path.relpath(f, staging).startswith("_check")]
    for m in res.manifests:
        if m.get("file"):
            rel = os.path.relpath(m["file"], staging)
            # a set built for the self-check only was not delivered: it has no file
            m["file"] = "" if rel.startswith("_check") else os.path.join(deliver, rel)
    res.files = delivered
    res.deliverables = deliver
    reg.save()
    names.save()
    with open(os.path.join(res.log_dir, "proof_records.json"), "w", encoding="utf-8") as fh:
        json.dump(trail.to_dict(), fh, indent=1)
    flags = any(s.status == M.FLAG for s in res.stages)
    status = RESULT_FLAGS if flags else RESULT_PASS
    res.status = status
    st.say(f"{len(delivered)} file(s) in {deliver}"
           + ("; the run report is added beside them" if opts.out_report else ""))
    done("Deliver")
    out = finish(status)
    if opts.out_report:
        rdir = os.path.join(deliver, "report")
        os.makedirs(rdir, exist_ok=True)
        for name, src in (("run_report.txt", out.report_txt), ("run_report.pdf", out.report_pdf)):
            if src and os.path.exists(src):
                shutil.copyfile(src, os.path.join(rdir, name))
                res.files.append(os.path.join(rdir, name))
    return out
