# Planloom beta: read me first

Thanks for testing. Planloom is a plan-set workspace for the trades. It
runs entirely on your computer and **never connects to the internet**,
so your drawings stay on your machine.

## Install (one minute)

1. Download **Planloom.exe** (or the zip and unzip it).
2. Put it anywhere, for example your Desktop.
3. Double-click it.

That's it: nothing to install, no account, no admin rights.

**If Windows shows a blue box saying "Windows protected your PC":** click
**More info**, then **Run anyway**. It appears because this beta is not
yet signed with a paid certificate, not because anything is wrong. Some
antivirus tools are cautious with new single-file programs too; allow it
if you are asked.

The first launch can take 10 to 20 seconds while it unpacks. Later
launches are faster.

## Needs

- Windows 10 or 11, 64-bit.
- About 200 MB of free space.

## Start here: the Batterboard (two minutes, no drawings needed)

The newest part, and the one we most want eyes on. It turns a plan set
into layout sheets, point files, an open items list and a plain report
of what was checked.

1. Click **Field Management** at the top, then the last tab, **The
   Batterboard**.
2. Click **Try the sample set** (top right). It writes a small made-up
   building into your Documents folder and loads it. Nothing of yours is
   needed.
3. Look at the table: it shows what each sheet was read as.
4. Click **RUN THE BATTERBOARD**. Nine lights fill in, green for pass,
   amber for flag. It should finish in under a minute with **PASS WITH
   FLAGS** (the sample building has disagreements planted in it on
   purpose, and the tool is meant to find them).
5. Click **Open the plates**, **Open the run report** and **Open Output
   Folder** and look around.

Then, if you can, try it on a real vector plan set of your own. This
part is brand new and has only ever seen the made-up building, so a real
set will very likely be refused or flagged in places. That is exactly
what we need to hear about. A result of **BLOCKED** is the tool saying
"I could not check this, so I delivered nothing". It is not a crash.
Every sheet it makes is stamped FOR REVIEW: do not build from them.

One thing to know: if you still have the plates open in a PDF viewer
and press Run again, the run may stop at the last light (**Deliver**)
and say the folder could not be replaced. Close the PDF and press Run
again. Nothing is lost.

What it does not do yet: scanned sheets (it reads drawings saved as
vector PDF), elevations, slab steps and depressions, curbs and pads,
embeds and hold-downs. Those switches are greyed out and say "not built
yet".

## What else to try

- **Stamp RFIs onto a plan set** (Project Management): drag your plan set
  PDF and your RFI PDFs in, check the sheet mapping table, press Run.
  Every stamped page is pixel-checked so nothing covers linework; the
  report ends in PASS.
- **Compare two revisions** of a sheet and get a redline.
- **Merge, split, rotate and repair** PDFs.
- **Make a scanned PDF searchable** (built-in text reading).
- **Layout points by hand** (Plans & BIM, Fieldstitch Layout): place
  numbered points on a plan and export kits for field tablets.

## Tell us what happened

Send these to the person who gave you this file:

- What you were doing, and what you expected.
- What actually happened (a screenshot helps).
- The `*_report.txt` file if a stamp run produced one.
- For the Batterboard: the `run_report.txt` from the `run_log` folder
  inside your output folder. It is written even when the run is blocked,
  and it holds no drawings, only what was read and checked.

Please **don't** send drawings from a job you are not allowed to share.
A description or a screenshot with the title block cropped out is enough.

`planloom-cli.exe` is the same engine for the command line. Most testers
can ignore it.
