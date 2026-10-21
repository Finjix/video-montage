# Packaging

`package` burns the subtitles and overlays into each clean video, mixes the BGM, checks the encode and publishes
it. Everything has a working default; the config only records your choices.

## Config (`package --config <json>`, stored as `temp/config/packaging.json`)

```json
{"defaults": {"font": "w8", "game_flower": "ice2",
              "text_pins": ["D:/pack/文字钉/WZD02.png", "D:/pack/文字钉/WZD09.png"],
              "disclaimer": "D:/pack/免责/竖版免责声明.png",
              "bgm": ["D:/pack/bgm/a.mp3", "<path printed by fetch-bgm>"]},
 "outputs": {"P01": {"text_pin": "D:/pack/文字钉/WZD05.png", "bgm": "D:/pack/bgm/a.mp3",
                     "cues": [{"index": 1, "entrance": "shout_wave"},
                              {"index": 4, "text": "温度就是头等大事", "y": 0.82},
                              {"index": 6, "color": "white", "spans": [{"start": 2, "end": 4, "color": "yellow"}]}]}}}
```

- Lists in `defaults` are used round-robin over the outputs; an output's own value wins.
- Without a config: text pins rotate through the asset pack's pin folder (`文字钉`/`pin`), the single PNG in a
  `免责`/`disclaimer` folder is the disclaimer, and the BGM rotates through the pack's audio and registered downloads.
- Nameplates come from the plan (first solo appearance) and the work-order registry; you do not configure them.
- A cue override names its 1-based `index`; add the cue `text` to have the override skipped (with a note) if that
  cue's text later changes. Fields: `color`, `spans`, `font_size`, `entrance`, `x`, `y`, `max_width`.

## Text pin and disclaimer

Exactly one text pin per video, shown for its whole length. Choose pins whose copy fits the video's message
(read them in `catalog/assets.jpg`), spread them over the batch, and check on the packaged sheet that the pin does
not cover faces, subtitles or nameplates. The disclaimer covers the whole video. Overlays are the pack's 9:16
transparent PNGs; never create or edit artwork.

## Subtitle design

The library (`assets/packaging/design/catalog.json`); only select from it, never add effects:

| Kind | IDs | Use |
| --- | --- | --- |
| font | `w8` (default, heavy), `fangtang` (rounder) | one font per video |
| flower | `ice2` (default game-name flower), `ice1`, `fire1` | game names; `fire1` for hot emotion (larger files) |
| entrance | `none`, `bounce_up`, `shout_wave`, `ice_drift` | quiet narration / short positive hooks / emotional peaks (about 5 characters max) / icy mood and game names |

Defaults: every cue yellow at size 8, every game name in its flower at size 9, the first cue with an entrance
(rotating over the batch), a standalone game-name cue with a 200 ms `ice_drift`, the rest static. Batches of 3+
videos must use all three entrances, and with 6+ animated cues no entrance may exceed 70%; `package` adds a missing
one on a short quiet cue when needed.

Style rules (validated): ordinary text is yellow `#FFDE00`; white `#FFFFFF` only beside yellow emphasis in the same
cue; sizes 8 and 9 only (game names 9); one line, at most 10 characters; an entrance settles within half of its cue;
at most one standalone game-name cue per video, on screen at least 0.8 s. Keep most narration static and give
entrances to the opening, key selling points and emotional lines; avoid strong effects back to back.

Layout: subtitles sit at `y` 0.69 by default. In an extreme close-up (face filling the frame, mouth near that
height) set that cue's `y` to about 0.80-0.84 so the text sits under the chin and clear of the disclaimer; confirm
on the packaged sheet.

## Subtitles

The draft follows the corrected words: cues of at most 10 characters, aimed at 1.2-2.5 s, never splitting a word,
a number from its classifier, a reduplication (蹭蹭) or a library term, and never starting with a dependent particle
(的, 了...). Read the drafts; merge or re-split cues in the TXT when the meaning reads badly. Cues shorter than 0.8 s
and dependent heads are reported as notes.

## BGM

**Style:** light, playful, fashionable, strong groove and a clear beat, like a commercial ad (about 115-125 BPM,
punchy: dance-pop, synth-pop, funk, disco, upbeat house). Calm, ambient, chill, lounge, piano, orchestral or
children's music is wrong even when licensed. State in the summary why each track fits.

**Repeats:** one track serves at most eight videos of a batch (9-16 videos need 2 tracks, 40 need 5). `package`
hands the tracks out round-robin over every available track, so more tracks mean less repetition.

**Origins:** only the pack's own audio or a registered download. `bgm-assets` prints the inventory,
`tracks_needed` and whether a download is required. The agent fills any shortfall itself and never asks the user for
music: pack has no audio or fewer tracks than `tracks_needed`: download the missing tracks (the user's standing
permission), taking them from several different sources and creators rather than one site or one artist. Only the
user's own yes (`decide --key bgm_repetition`) lets a track serve more than eight videos.

**Licenses:** commercial use with no attribution, share-alike or no-derivatives terms: `cc0`, `public-domain`,
`pixabay` (Pixabay Content License), `mixkit` (Mixkit License). Read the license on the track's own page. Sources to
try (mix them): Mixkit, Pixabay Music, Free Music Archive (CC0 filter), Internet Archive, Wikimedia Commons. Register each
track (the file is checked for audio, length >= 20 s and level, and the license evidence is kept in `config/bgm/`):

```text
& "<plugin_root>/scripts/video_montage.cmd" fetch-bgm --job-dir <job_dir> --url <direct audio link>
  --license mixkit --license-url <license page> --license-evidence "<statement quoted from the page>"
  --source-page <track page> --title "<title>" --creator "<artist>" --instrumental
```

Every BGM the agent pulls during production lives inside `<job_dir>` (the `temp` folder), never beside the videos
or elsewhere on disk: `fetch-bgm` stores the registered file under `<job_dir>/config/bgm/`. Use `--file <path>` for a
track downloaded another way, and download it into `<job_dir>/config/bgm/` first (not the output root, Downloads or the
system temp), then delete any stray copy after registering; `--asset-audio-unsuitable "<reason>"` when the pack's audio
cannot serve as music (only sound effects, for example).

## Loudness

Voices are balanced per clip at render time. `package` sets the BGM gain so the voice stays at least 4 dB above the
music, measures the encode and corrects the mix once so the finished video lands at -23 LUFS (+/- 1 LU, true peak
at most -1 dBTP). Re-running `package` after a rule change rebuilds every changed output.
