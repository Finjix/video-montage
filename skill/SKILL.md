---
name: video-montage
description: Produce, revise and reburn batches of Chinese spoken-word celebrity montage videos (17-30 s, 1440x2560, 60 fps) from source footage, with subtitles, nameplates, text pins, disclaimer and BGM.
---

# Video montage

## Runtime

- Run everything with the bundled Python from this folder (the suite root):
  `assets/dependencies/python/python.exe scripts/video_montage.py <command> --job-dir <job_dir> ...`
- The scripts do all media work, timing and checks. Your job is the editorial judgment: read the catalog and
  sheets, correct the transcript, tag people, compose the plan and the packaging choices. Never write your own
  search, render or review scripts; `check` answers in under a second, so iterate with it.
- Write every file you author (annotations, plan, packaging config) in English, except speech and subtitle text.
  Reply to the user in the user's language.

## Workflow

Read [workflow](references/workflow.md) for commands and file formats; [editing](references/editing.md) for the
editorial rules; [packaging](references/packaging.md) before `package`.

1. `init --work-order -` (JSON on stdin) creates `<Desktop>/自动化混剪_YYYYMMDD_HHMMSS/temp` (the job dir).
2. `prepare` (about 1-2 min) writes `temp/catalog/catalog.md`, one sheet per source and `assets.jpg`.
3. Read the whole catalog and every sheet, then `annotate --file` with people per source/shot and transcript fixes.
4. Write the plan and run `check --plan` until it reports no problems.
5. `render --plan` makes the clean videos, subtitle drafts and one cut sheet per output. Look at every cut sheet
   and subtitle file; fix the plan or the subtitle files and re-run (only changed outputs are redone).
6. `package [--config]` packages, checks and publishes every video into the delivery folder. Look at the packaged
   sheets; adjust the config and re-run if a layout covers a face or a cue reads badly.

## Rules

1. **Honest work.** Judge from the catalog, sheets and transcripts you actually read. Never claim you listened,
   that a person reviewed, or that something passed that you did not check.
2. **ASR is a draft.** Correct misrecognitions in `annotate` from context, the fixed spellings in
   `assets/text-library.json` and the scene. When unsure, choose the most likely words and continue; list those
   inferences and their basis in the final summary. Never ask the user for a transcript and never put words in a
   speaker's mouth (no ad copy, no invented lines).
3. **Edit shape.** Each video is 17-30 s at natural speed, built from complete sentences with at least four real
   camera shots; no freezes, slow motion, repeated frames, half sentences or filler. `check` enforces the
   frame-level rules (clean silent cuts, stable edges, shot lengths, user-rejected intervals).
4. **Coherence first.** Every cut must make logical before-and-after sense. When coherence conflicts with
   variety or cast mix, coherence wins. There is no single/multi-celebrity ratio.
5. **Whole batch.** Deliver exactly the requested count. Every video uses at least three different source files,
   and the batch stays within the reuse limits. When the footage truly cannot meet a limit, or the asset pack has
   too few BGM tracks for the 67% share rule, stop and ask the user once with the numbers; record only their answer
   with `decide`. Never grant yourself an exception.
6. **Packaged by default.** A batch request means finished packaged videos (subtitles, nameplates, one text pin,
   disclaimer, BGM). Never ask whether to package. Deliver unpackaged (`package --clean`) only when the user says so.
   BGM is the pack's audio; if the pack has none, download a licensed commercial-ad style track yourself.
7. **Persist.** Keep fixing and re-running until every requested video is published. Ask the user only for an
   absolute blocker (say what you tried and the minimum you need). Uncertain speech, long processing or a failed
   check are never blockers.
8. **Delivery folder.** It holds only `<plan_id>.mp4` files and `temp/`. Keep your inputs in `temp/config/` and
   any scratch work in `temp/`; leave nothing elsewhere. Revisions and subtitle reburns reuse the same folder.
