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
- Without a config: text pins rotate through the pack's pin folder (`文字钉`/`pin`), the single PNG in a
  `免责`/`disclaimer` folder is the disclaimer, and the BGM rotates through the pack's audio and registered downloads.
- Nameplates come from the plan (first solo appearance) and the work order; they are not configured here.
- A cue override names its 1-based `index`; adding the cue `text` makes the override skip (with a note) if that cue's
  text changes later. Fields: `color`, `spans`, `font_size`, `entrance`, `x`, `y`, `max_width`.

## Overlays

Exactly one text pin per video, shown for its whole length: choose pins whose copy fits the video's message (read
them in `catalog/assets.jpg`), spread them over the batch, and check on the packaged sheet that the pin covers no
face, subtitle or nameplate. The disclaimer covers the whole video. Overlays are the pack's transparent 9:16 PNGs;
never create or edit artwork.

## Subtitle design

Select only from the library (`assets/packaging/design/catalog.json`):

| Kind | IDs | Use |
| --- | --- | --- |
| font | `w8` (default, heavy), `fangtang` (rounder) | one font per video |
| flower | `ice2` (default game-name flower), `ice1`, `fire1` | game names; `fire1` for hot emotion (larger files) |
| entrance | `none`, `bounce_up`, `shout_wave`, `ice_drift` | quiet narration / short positive hooks / emotional peaks (at most 5 characters) / icy mood and game names |

Defaults: every cue yellow `#FFDE00` at size 8, game names in their flower at size 9, the first cue with an entrance
(rotating over the batch), a standalone game-name cue with a 200 ms `ice_drift`, the rest static. A batch of 3+
videos uses all three entrances (`package` adds a missing one on a short quiet cue), and with 6+ animated cues no
entrance exceeds 70%.

Validated style: white `#FFFFFF` only beside yellow emphasis in the same cue; sizes 8 and 9 only; one line of at most
10 characters; an entrance settles within half of its cue; at most one standalone game-name cue per video, on screen
at least 0.8 s. Keep most narration static; give entrances to the opening, key selling points and emotional lines,
never strong effects back to back.

Layout: subtitles sit at `y` 0.69. In an extreme close-up (face filling the frame, mouth near that height) set the
cue's `y` to about 0.80-0.84, under the chin and clear of the disclaimer, and confirm on the packaged sheet.

## Subtitle drafts

Drafts follow the corrected words: cues of at most 10 characters, aimed at 1.2-2.5 s, never splitting a word, a
number from its classifier, a reduplication (蹭蹭) or a library term, never starting with a dependent particle
(的, 了...). Read every draft and merge or re-split cues where the meaning reads badly (e.g. 那战力可不得 / 蹭蹭往上涨啊,
not 那战力可 / 不得蹭蹭往上涨啊). Cues under 0.8 s and dependent heads are reported as notes.

## BGM

**Style:** light, playful, fashionable, strong groove and a clear beat, like a commercial ad (about 115-125 BPM:
dance-pop, synth-pop, funk, disco, upbeat house). Calm, ambient, chill, lounge, piano, orchestral or children's music
is wrong even when licensed.

**Count:** one track serves at most eight videos of a batch (40 videos need 5 tracks); `package` hands tracks out
round-robin. `bgm-assets` prints the inventory, `tracks_needed` and whether downloads are required. Fill any
shortfall yourself, from several different sources and creators. Only a `bgm_repetition` decision lets one track
serve more than eight videos.

**Licenses:** commercial use with no attribution, share-alike or no-derivatives terms: `cc0`, `public-domain`,
`pixabay` (Pixabay Content License), `mixkit` (Mixkit License). Read the license on the track's own page. Sources to
mix: Mixkit (direct `https://assets.mixkit.co/music/<id>/<id>.mp3`), Internet Archive CC0 items
(`archive.org/metadata/<id>`, `archive.org/download/<id>/<file>`), Wikimedia Commons CC0 audio, Free Music Archive
(CC0 filter), Pixabay Music. Register each track:

```text
& "<plugin_root>/scripts/video_montage.cmd" fetch-bgm --job-dir <job_dir> --url <direct audio link>
  --license mixkit --license-url <license page> --license-evidence "<statement quoted from the page>"
  --source-page <track page> --title "<title>" --creator "<artist>" --instrumental
```

`fetch-bgm` downloads with a browser user agent and the source page as referer (falling back to the system curl),
checks the audio (length >= 20 s, level) and stores the file and its license evidence in `<job_dir>/config/bgm/`.
For a file you fetched another way, download it into `<job_dir>/config/bgm/` first and pass `--file <path>`; leave no
copy elsewhere. `--asset-audio-unsuitable "<reason>"` registers downloads even though the pack has audio that cannot
serve as music (only sound effects, for example).

## Loudness

Voices are balanced per clip at render time. `package` sets the BGM gain so the voice stays at least 4 dB above the
music, then corrects the mix until the finished video lands at -23 LUFS (+/- 1 LU, true peak at most -1 dBTP).
