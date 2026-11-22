# START_HERE.md: where everything is

## The two programs

| | **Planloom** (this repo) | **TradeForge** (the owner's private workshop repo) |
|---|---|---|
| What | Offline plan-set workspace: RFI stamping with pixel-checked verification, revision compare, OCR, layout points + field export kits, drafting, peer checker, trade knowledge base | Offline trade "operating system": project database, registers, spec/submittal writer, plumbing sizing from DXF, apprentice tutor |
| Language | Python (PyMuPDF + numpy only) | JavaScript, desktop shell + SQLite |
| Size / maturity | ~73,000 lines, 76 test scripts all green, 45 shipped rounds, v5.11.0 | ~33,000 lines, B1 to B10 milestones mostly done |
| AI | None needed; every engine is deterministic | Generative parts need a local language model installed separately |
| Ships as | Single `Planloom.exe`, double-click | Linux portable image; a Windows build was never produced |

## Decision: keep them separate, ship Planloom

- Different languages. Merging means rewriting one of them, which is
  months of work for no new capability.
- They already share what matters: Heartwood imports the TradeForge
  knowledge base (`heartwood/ingest.py`), and both carry the same
  meaning-search layer (called Journeyman on the TradeForge side).
- Planloom is the one that runs on a crew's Windows laptop with nothing
  to install, and the layout package brief targets it module by module.
- TradeForge is parked, not deleted. Port single engines into Planloom
  later if testers ask for them (DXF fixture takeoff and pipe sizing are
  the likely first picks).

## Where the work stands

- `LAYOUT_PACKAGE_BRIEF.md`: the owner's brief for the one-click layout
  package (the Batterboard).
- `BATTERBOARD_PLAN.md`: the phased plan. **All eight phases are built**
  (v5.11.0). Its section 6 is the plain record: what was built, how it was
  tested, what was NOT verified, and the choice made on each of the five
  owner questions.
- The Batterboard is in the app: **Field Management, last tab, "The
  Batterboard"**. The fastest first look is its **Try the sample set**
  button, then **RUN THE BATTERBOARD**.
- The one thing nobody but the owner can do: run it on a real plan set.
  It has only ever seen the made-up sample building.
- `HANDOFF.md`: session continuity, naming registry, round notes.
- `CLAUDE.md`: the invariants. They win over every brief.

## Getting a beta out to testers

0. Every push to the working branch already builds and tests both exes;
   grab them from that run's Artifacts on the repository's Actions page.
1. To cut a release: on the repository website, Releases, Draft a new
   release, type a new tag such as `v5.11.0-beta.1`, target the branch
   `claude/planloom-session-resume-p10twg`, tick "Set as a pre-release",
   Publish. (This session's network policy blocks creating tags or
   releases from the cloud, so this one click is the owner's.)
2. The Windows build workflow then builds `Planloom.exe` and
   `planloom-cli.exe`, smoke-tests the source and the built exes, and
   attaches the exes, `Planloom-Windows.zip` and `SHA256SUMS.txt` to the
   release. If any test fails, nothing is attached.
3. Send testers the release page link. `BETA_TESTERS.md` rides inside the
   zip as `READ_ME_FIRST.md`.

## Owner checklist (things only the owner can do)

- [ ] Publish the release (step 1 above). It is one form on the website.
- [ ] Download `Planloom.exe` from the release on your own Windows PC and
      double-click it. Confirm it opens and a stamp run ends in PASS.
- [ ] In Field Management, open **The Batterboard**, press **Try the
      sample set**, then **RUN THE BATTERBOARD**. It should end in PASS
      WITH FLAGS in about a minute. Open the plates and the run report
      and see whether they read the way you want.
- [ ] Try a real drag-and-drop from the desktop (the one thing a server
      build cannot test).
- [ ] Run the Batterboard on one real plan set, on your own machine. Never
      commit that set. Whatever it refuses, flags wrongly or misreads is
      the list for the next round; a blocked run is the tool doing its
      job, not a crash.
- [ ] Look at the plate style: switch on "One-sheet proof only", run, and
      say yes or no. Plates stay stamped FOR REVIEW until you switch on
      Issue.
- [ ] Read the five decisions in `BATTERBOARD_PLAN.md` section 6 and say
      if any should go the other way.
- [ ] Later, optional: a code-signing certificate removes the "Windows
      protected your PC" warning testers see today.
