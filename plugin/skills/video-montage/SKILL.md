---
name: video-montage
description: Produce, revise and reburn batches of Chinese spoken-word (口播) celebrity montage videos (17-30 s, 1440x2560, 60 fps) from source footage, with subtitles, nameplates, text pins, disclaimer and BGM.
---

# Video montage

## Runtime

- Windows 10/11 x64 with a local executor. The plugin root is two directories above this file.
- Every command: `& "<plugin_root>/scripts/video_montage.cmd" <command> --job-dir <job_dir> ...`. Python, FFmpeg,
  libraries and models download into the plugin's `runtime-cache` on first use; never install or build an
  environment yourself. Results go to stdout, progress to stderr.
- Exit 2: the input broke a rule; fix what the message lists and run the same command again. Exit 3: runtime or media
  failure; read the cause and run the same command again. Every command can be re-run and redoes only what changed.
- The scripts do all media work, timing, route search and checking. Your part is editorial: transcript fixes,
  people, beats, which joins a viewer would follow, packaging choices. Never write your own search, render or
  review scripts.
- Files you write (annotations, joins, plan, packaging config) are English, except speech and subtitle text. Reply
  to the user in the user's language.

## Talking to the user

Confirm once, at the start, in a single message, and only what you cannot work out yourself: the footage, the asset
pack, the count, the celebrities and their nameplates. Add any gap `init` reports (a missing disclaimer or text pin,
too few source files). If everything is clear, ask nothing and start.

From then on, work to the published batch without asking. An open question never stops production: take the
default below, carry on, and report it in the final summary.

| Situation | Default |
| --- | --- |
| Unclear speech | the most likely words from context, the scene and `<plugin_root>/assets/text-library.json` |
| Who is on screen | judge from the sheets (folder names are only a hint); tag doubtful shots so they are not planned |
| Too few coherent routes for the count | work down the ladder in [editing](references/editing.md#reaching-the-count) |
| Too few BGM tracks in the pack | download licensed tracks yourself ([packaging](references/packaging.md#bgm)) |
| BGM downloads fail from at least three different sources | `decide --key bgm_repetition --value true --instruction "fallback: <sources tried>"` |
| The ladder is exhausted and videos still lack three sources | `decide --key min_sources_per_output --value 2 --instruction "fallback: <why>"` for the rest |
| Layout, text pin, entrance or font choice | the packaging defaults, adjusted only after looking at the sheets |

Stop to ask only when nothing can continue: the sources are missing or unreadable, or the first-use download has no
network. Say what you tried and the minimum you need.

## Workflow

Commands and file formats: [workflow](references/workflow.md). Editorial rules: [editing](references/editing.md).
Packaging: [packaging](references/packaging.md).

1. `init --work-order -` (JSON on stdin) creates `<output_root>/自动化混剪_YYYYMMDD_HHMMSS/temp`, the job dir.
2. `prepare` analyses the footage (about 1 min per 10 sources once cached). While it runs, settle the BGM:
   `bgm-assets`, then `fetch-bgm` for any shortfall.
3. Read the whole catalog and every sheet, then `annotate --file` people, transcript fixes and the beats of every
   source.
4. `routes` lists every clip that can be cut cleanly and every join that occurs in a route passing all machine
   checks. Judge the joins, write the ones you accept with their relation and reason, then `routes --joins <file>`
   writes a draft plan for the whole batch within the batch limits.
5. `check --plan` the draft and read `reports/scripts.md` as a viewer; replace any video that does not read as one
   argument. `render --plan` makes the clean videos, subtitle drafts and cut sheets; look at every sheet and subtitle
   file and fix the plan or the subtitle files.
6. `package [--config]` packages, checks and publishes every video. Look at the packaged sheets; adjust the config
   and re-run when a layout covers a face or a cue reads badly.
7. Final summary: videos delivered and the folder; transcript inferences and their basis; weak routes by `plan_id`;
   BGM tracks with license and why each fits; every fallback default you took.

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
6. **Packaged by default.** A batch means finished videos with subtitles, nameplates, one text pin, disclaimer and
   BGM. Deliver unpackaged (`package --clean`) only when the user asks.
7. **Delivery folder.** It holds only `<plan_id>.mp4` files and `temp/`. Your inputs live in `temp/config/`,
   downloads in `temp/config/bgm/`, scratch work in `temp/`. Revisions and reburns reuse the same folder.
