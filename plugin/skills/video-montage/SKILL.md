---
name: video-montage
description: Produce, revise and reburn batches of Chinese spoken-word (口播) celebrity montage videos (17-30 s, 1440x2560, 60 fps) from source footage, with subtitles, nameplates, text pins, disclaimer and BGM.
---

# Video montage

## Runtime

- Requires Windows 10/11 x64 and a local executor. Resolve the plugin root as two directories above this SKILL.md.
- Run `& "<plugin_root>/scripts/video_montage.cmd" <command> --job-dir <job_dir> ...`.
  Python, media tools, dependencies and models download automatically only when needed. First use needs network access;
  subsequent use reuses the external user cache. Progress goes to stderr; results go to stdout.
  Exit 2 is an input/rule failure; exit 3 is a runtime/download failure: read the cause and retry the same command.
  Do not install dependencies or write temporary files into the plugin. Never recreate a bundled environment.
- The scripts do all media work, timing and checks. Your job is the editorial judgment: read the catalog and
  sheets, correct the transcript, tag people, cut every source into beats, compose the plan and the packaging
  choices. Never write your own search, render or review scripts; `check` answers in under a second, so iterate with it.
- Write every file you author (annotations, plan, packaging config) in English, except speech and subtitle text.
  Reply to the user in the user's language.

## Workflow

Read [workflow](references/workflow.md) for commands and file formats; [editing](references/editing.md) for the
editorial rules; [packaging](references/packaging.md) before `package`.

1. `init --work-order -` (JSON on stdin) creates `<Desktop>/自动化混剪_YYYYMMDD_HHMMSS/temp` (the job dir).
2. `prepare` (after dependencies are cached, about 1-2 min) writes `temp/catalog/catalog.md`, one sheet per source and `assets.jpg`.
3. Read the whole catalog and every sheet, then `annotate --file` with people per source/shot, transcript fixes and
   the **beats** of every source (the self-contained thoughts a video may use, each marked talk or scene).
4. Write the plan and run `check --plan` until it reports no problems; then read `temp/reports/scripts.md`, every
   video as a viewer hears it, and fix each join that does not follow.
5. `render --plan` makes the clean videos, subtitle drafts and one cut sheet per output. Look at every cut sheet
   and subtitle file; fix the plan or the subtitle files and re-run (only changed outputs are redone).
6. `package [--config]` packages, checks and publishes every video into the delivery folder. Look at the packaged
   sheets; adjust the config and re-run if a layout covers a face or a cue reads badly.

## Rules

1. **Honest work.** Judge from the catalog, sheets and transcripts you actually read. Never claim you listened,
   that a person reviewed, or that something passed that you did not check.
2. **ASR is a draft.** Correct misrecognitions in `annotate` from context, the fixed spellings in
   `<plugin_root>/assets/text-library.json` and the scene. When unsure, choose the most likely words and continue; list those
   inferences and their basis in the final summary. Never ask the user for a transcript and never put words in a
   speaker's mouth (no ad copy, no invented lines).
3. **Spoken word first.** The product is a talking-to-the-viewer video: celebrities speak the message to camera.
   Acted scenes (reactions, gags, banter, role-play) are not the product: a video contains none. A reply to another
   character is fine when it stands alone as a message to the viewer. Mark every beat `talk` or
   `scene` honestly (see editing); scene beats can never be planned.
4. **Edit shape.** Each video is 17-30 s at natural speed, built from complete sentences with at least four real
   camera shots; no freezes, slow motion, repeated frames, half sentences or filler. `check` enforces the
   frame-level rules (clean silent cuts, stable edges, shot lengths, user-rejected intervals).
5. **Coherence first.** A video is one short spoken argument: opening, body, payoff or call to action. Every join
   must make sense to a viewer who has not seen the sources: the second clip answers, continues, proves or pays off
   the first. Clips are whole beats; a question, teaser or sign-off never lands where its answer or setting is
   missing. When coherence conflicts with variety, cast mix or a batch limit, coherence wins; find another route
   through the beats (rule 6) instead of padding a batch with broken joins.
6. **Whole batch: the count is not negotiable.** Deliver exactly the requested count. Never stop short, never edit
   `requested_outputs`, never offer the user a smaller batch or a menu of rules to loosen. Every video uses at least
   three different source files and the batch stays within the batch limits; the user has already allowed source reuse
   up to those limits, so never ask about reuse. When you have found fewer coherent videos than the count, you have not
   finished: work down the ladder in [editing](references/editing.md#reaching-the-count) (annotate every source, cut
   finer beats, recombine them, pair shots of like length) until the count is met. Weak joins are the last rung, not
   the first; deliver them labelled, never silently and never instead of the count.
7. **Packaged by default.** A batch request means finished packaged videos (subtitles, nameplates, one text pin,
   disclaimer, BGM). Never ask whether to package. Deliver unpackaged (`package --clean`) only when the user says so.
   BGM is the pack's audio. One track serves at most eight videos of a batch (40 videos need at least five tracks);
   when the pack has none or too few, download the missing licensed commercial-ad style tracks yourself, from several
   different sources, without asking. Downloaded BGM files stay in the job's `temp` folder (`config/bgm/`), never
   in the output root or elsewhere.
8. **Persist.** Keep fixing and re-running until every requested video is published. Ask the user only for an
   absolute blocker (say what you tried and the minimum you need). Uncertain speech, long processing, a failed check
   or too few coherent routes so far are never blockers.
9. **Delivery folder.** It holds only `<plan_id>.mp4` files and `temp/`. Keep your inputs in `temp/config/` and
   any scratch work in `temp/`; leave nothing elsewhere. Revisions and subtitle reburns reuse the same folder.
