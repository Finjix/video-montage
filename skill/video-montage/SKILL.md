---
name: video-montage
description: Produce, revise and reburn batches of Chinese spoken-word (口播) celebrity montage videos (17-30 s, 1440x2560, 60 fps) from source footage, with subtitles, nameplates, text pins, disclaimer and BGM. Use when the user asks for 混剪/口播混剪 videos cut from celebrity footage, or to edit, re-subtitle or reburn videos of an earlier montage batch. Windows only.
---

# Video montage

## Runtime

- Windows 10/11 x64 with a local shell. `<skill_root>` is the folder that holds this file.
- Every command: `& "<skill_root>/scripts/video_montage.cmd" <command> --job-dir <job_dir> ...`. Python, FFmpeg,
  libraries and models download into `<skill_root>/runtime-cache` on first use; never install or build an
  environment yourself. Results go to stdout, progress to stderr.
- The first `prepare` on a machine downloads about 10 GB (PyTorch, two speech models) and can take 30-60 min; it
  prints progress lines. Wait for it to finish (settle the BGM meanwhile); never inspect or poll `runtime-cache`.
- Read the output, not the exit code (some shells report every failure as 1). `error:` or a list of problems: the
  input broke a rule; fix what it lists and run the same command again. `runtime error:` / `media error:`: fix the
  cause it names (network, disk, a moved file) and run it again. A Python traceback is a skill bug: do not retry it
  unchanged; change the input that triggers it and name it in the final summary. Every command can be re-run and
  redoes only what changed.
- The scripts do all media work, timing, route search and checking. Your part is editorial: transcript fixes,
  people, beats, which joins a viewer would follow, packaging choices. Never write scripts that search routes,
  generate joins, render, review, or rewrite plans and subtitles in bulk: edit those files yourself, one by one.
- Files you write (annotations, joins, plan, packaging config) are English, except speech and subtitle text. Reply
  to the user in the user's language.

## Talking to the user

Always confirm before any command that does media work, in a single message, even when the request looks complete:
the footage (files and count), the asset pack (text pins, the one disclaimer, BGM tracks found), the number of
videos, the celebrities and their nameplates, and that every video is delivered fully packaged. List every gap
(a missing disclaimer, text pin or nameplate, too few source files) and ask the user to fill it. Run `init` before
asking so the message can name what the pack holds; `init` refuses an asset pack without text pins or with other
than one disclaimer, listing every gap at once. When the user fills gaps or changes the order, run `init` again
with `--job-dir` of the first run: it replaces that job until `prepare` has run, so no second folder is left behind.
Start `prepare` only after the user confirms and every gap is filled. The exception is BGM:
a shortfall is yours to download, so name it in the message but do not wait for it.

From then on, work to the published batch without asking. An open question never stops production: take the
default below, carry on, and report it in the final summary.

| Situation | Default |
| --- | --- |
| Unclear speech | the most likely words from context, the scene and `<skill_root>/assets/text-library.json` |
| Who is on screen | judge from the sheets (folder names are only a hint); tag doubtful shots so they are not planned |
| Too few coherent routes for the count, or a registered celebrity in no video | work down the ladder in [editing](references/editing.md#reaching-the-count) |
| Too few BGM tracks in the pack | download licensed tracks yourself ([packaging](references/packaging.md#bgm)) |
| BGM downloads fail from at least three different sources | `decide --key bgm_repetition --value true --instruction "fallback: <sources tried>"` |
| The ladder is exhausted and videos still lack three sources | `decide --key min_sources_per_output --value 2 --instruction "fallback: <why>"` for the rest |
| Layout, nameplate spot, text pin, entrance or font choice | the packaging defaults, adjusted only after looking at the sheets |

Stop to ask only when nothing can continue: the sources are missing or unreadable, or the first-use download has no
network. Say what you tried and the minimum you need.

## Workflow

Commands and file formats: [workflow](references/workflow.md). Editorial rules: [editing](references/editing.md).
Packaging: [packaging](references/packaging.md).

1. `init --work-order -` (JSON on stdin) creates `<output_root>/自动化混剪_YYYYMMDD_HHMMSS/temp`, the job dir. Then
   send the confirmation message ([talking to the user](#talking-to-the-user)) and wait for the user's answer.
2. `prepare` analyses the footage (about 1 min per 10 sources once cached). While it runs, settle the BGM:
   `bgm-assets`, then `fetch-bgm` for any shortfall.
3. Read the whole catalog and every sheet, then `annotate --file` people, transcript fixes and the beats of every
   source, all in one file. Cut beats fine (one sentence where it stands alone) and give beats that make the same
   point the same `topic`, so every slot of a script has stand-ins from several sources.
4. `routes` lists every clip that can be cut cleanly and every join that occurs in a route passing all machine
   checks (`reports/routes.md`; read all of it, in parts when it is long). Judge each join on its own words, write
   the ones you accept (clip to clip, or topic to topic when every pair reads) with a relation and a reason that
   quotes the linking words of both sides, then `routes --joins <file>` composes a draft plan for the whole batch
   within the batch limits (no two videos share more than 25% of their positions) and prints the cast split.
5. `check --plan` the draft and read `reports/scripts.md` as a viewer. To replace a video, change the joins (or the
   beats) and run `routes --joins` again: videos still chosen keep their plan IDs and renders. `render --plan` makes
   the clean videos, subtitle drafts and cut sheets; look at every sheet and subtitle file and fix the annotations,
   joins or subtitle files.
6. `package [--config]` packages, checks and publishes every video. Look at the packaged sheets; adjust the config
   and re-run when a nameplate or layout covers a face or a cue reads badly.
7. Final summary, in your reply (not only in a file): videos delivered and the folder; videos per celebrity, and
   why any registered celebrity has none; transcript inferences and their basis; weak routes by `plan_id`; BGM
   tracks with license and why each fits; every fallback default you took.

## Rules

1. **Honest work.** Judge only from the catalog, sheets, transcripts and reports you read. Never claim you listened,
   that a person reviewed, or that a check passed when it did not.
2. **ASR is a draft.** Correct misrecognitions in `annotate`. Never put words in a speaker's mouth: no ad copy, no
   invented lines.
3. **Talk only.** Celebrities speak the message to camera. Acted scenes (reactions, gags, banter, role-play) are
   `scene` beats and never planned.
4. **Coherence first.** A video is one short spoken argument: opening, body, payoff or call to action, every join
   followed by a viewer who has not seen the sources. Coherence beats variety, cast mix and reuse limits.
5. **The count is the deliverable.** Deliver exactly the requested number of videos. Never edit
   `requested_outputs`, never stop short, never offer a smaller batch or a menu of rules to loosen. Source reuse is
   already allowed up to the batch limits.
6. **Every celebrity counts.** Each registered celebrity leads or joins videos when their footage allows; one with
   no video is a gap to work on, never an outcome to accept silently.
7. **Always packaged.** Every delivered video has subtitles, nameplates, one text pin, the disclaimer and BGM. There
   is no unpackaged delivery, even on request: `package` refuses a video missing any of them.
8. **Delivery folder.** It holds only `<plan_id>.mp4` files and `temp/`. `temp/` holds only what a revision or
   reburn needs, never for people: the pipeline's own folders, your current inputs in `temp/config/` (one file
   per purpose; overwrite drafts instead of keeping copies) and the chosen BGM in `temp/config/bgm/`. Logs, scratch
   scripts, downloaded pages and trial files also go in `temp/`.
   Revisions and reburns reuse the same folder.
