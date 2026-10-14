# Packaging design

After the subtitle review, Codex writes one `subtitle_design` per video from the speech, shots,
available space and rhythm, and judges it again on the actual packaged output. Codex only selects and
combines existing library effects; it never creates or modifies effects, animations, flower styles,
shaders or decorative assets, and the effect library is only extended on a separate explicit user
request. When nothing fits, use static subtitles. The renderer runs the existing implementations and
calls no model.

## Library

`assets/packaging/design/catalog.json` is the machine catalog. Summary:

| Kind | ID | Use |
| --- | --- | --- |
| font | `w8` (WenYue XinQingNian W8, 新青年体) | default, heavy strokes |
| font | `fangtang` (WenYue FangTang, 方糖体) | rounder alternative |
| flower | `ice1` | blue-white ice: icy themes, brand |
| flower | `ice2` | white-cyan gradient with purple glow: brand, cool mood (default `game_flower`) |
| flower | `fire1` | yellow-orange gradient with red edge: hot emotion, strong selling points |
| entrance | `none` | quiet: narration, explanation, dense information |
| entrance | `bounce_up` | medium: short highlights, positive hooks |
| entrance | `shout_wave` | strong: emotional peaks, rhetorical questions, warnings |
| entrance | `ice_drift` | medium: icy atmosphere, game names |

There is no fade. Removed effects (pop-and-settle, orange burst, repeated-text layouts, fire emotion
text, double-layer titles, info cards, two extra flowers and reference-derived text templates) must not
be reintroduced, including through task-made transparent images. W8 with whole-word `ice2` uses the
calibrated shader and motion; other fonts and mixed runs use the generic motion with the chosen glyphs
and colours.

## Rules

- **Font:** one main font per video; ordinary subtitles stay uniform.
- **Colour:** ordinary text is primarily yellow `#FFDE00`; plain narration may be all yellow. White
  `#FFFFFF` appears only together with yellow emphasis in the same cue (emphasized words yellow, the
  rest white); never an all-white ordinary cue, and a blue game-name flower does not count as the
  yellow partner.
- **Game names:** list every real game name in `game_names` (verified in speech, asset copy or the
  packaging request; `[]` for non-game videos). Every occurrence uses complete flower text at size 9
  in its calibrated palette; never split a name across cues.
- **Size:** only 8 and 9 (reference-calibrated). Ordinary text is 8. Game names are 9 and are the only
  mixed size allowed inside a cue; other emphasis at 9 must be a separate cue of uniform size, never
  enlarged words inside an 8 line. At 1440 px width, W8 is 108/122 px and FangTang 100/113 px.
- **Outline:** ordinary yellow/white text uses the calibrated CapCut black outline 40 (text scale 164%,
  12.3 px at 1440 width) for every font and size; flowers keep their own outline and glow.
- **Motion:** narration is mostly static. Use entrances for the opening, key selling points and
  emotional lines; avoid consecutive strong animations; never animate every cue or repeat text to
  show an effect. Choose by meaning: `ice_drift` for icy/game-name atmosphere, `shout_wave` for
  emotional emphasis, `bounce_up` for short positive hooks; never cycle blindly.
- **Batch variety:** batches of three or more outputs use all three animations, two-output batches at
  least two; once there are six or more animated cues, no single effect exceeds 70% of them. Check the
  whole batch distribution before rendering.
- **Standalone game name:** at most one cue per video may show only a game name, chosen by context so
  the neighbouring cues stay meaningful (no orphan fillers like 玩). It needs ≥ 0.8 s final display and
  ≥ 0.6 s settled reading, complete flower text at 9, preferably `ice_drift` with a 200 ms entrance
  (unaccelerated). Otherwise keep the name inside mixed text.
- **Layout:** avoid covering faces, mouths and on-screen game information.

## `subtitle_design`

```json
{
  "subtitle_sha256": "<reviewed subtitle sha256>",
  "reading_policy": "semantic-reading/v1",
  "font": "w8",
  "game_names": ["无尽冬日"],
  "game_flower": "ice2",
  "game_font_size": 9,
  "reason": "厚实字形适合口播，黄字为主，局部黄色重点配白色，游戏名冰字；关键短句入场后落稳。",
  "protected_regions": [{"box": [0.1, 0.05, 0.9, 0.3], "start_frame": 0, "end_frame_exclusive": 180}],
  "cues": [
    {"index": 1, "text": "来玩无尽冬日", "color": "yellow", "font_size": 8,
     "layout": {"x": 0.5, "y": 0.69, "max_width": 0.9}, "entrance": {"effect": "bounce_up", "duration_ms": 250}},
    {"index": 2, "text": "轻松解压", "color": "white", "spans": [{"start": 2, "end": 4, "color": "yellow"}],
     "entrance": {"effect": "none"}}
  ]
}
```

- Allowed keys only: `subtitle_sha256`, `reading_policy`, `font`, `game_names`, `game_flower`,
  `game_font_size`, `reason`, `cues`, `protected_regions` (plus `needs_design_review`, see below).
- `cues` covers every reviewed cue in order with 1-based `index` and identical `text`.
- `spans`: 0-based, end-exclusive character ranges with optional `color` (yellow/white), `flower`
  (`ice1`/`ice2`/`fire1`, never cutting a game name) and `font_size`. Game-name flower spans are added
  automatically. `line_breaks` must be absent or empty.
- `layout`: normalized centre `x`/`y` (default 0.5/0.69, range 0.02–0.98) and `max_width` (default 0.9,
  0.1–0.96). `layout.size` was removed. Text wider than one line is rejected: re-split the subtitles.
- `entrance`: `none` (0 ms) or an effect with integer `duration_ms` ≥ 50 and at most half the cue
  interval (default 350). `shout_wave` reserves 2.4× the text width for its motion, so a centred cue
  fits only about 5 characters at size 8 (4 at size 9); use it on short cues.
- `protected_regions`: normalized `[left, top, right, bottom]` boxes with unaccelerated 60 fps frame
  ranges. Motion leaving the canvas or entering a protected region is rejected.

The final review adds `design_pass: true` and a concrete `design_reason` after inspecting the actual
entrance frames, settled reading, cue starts/ends and overlaps with other layers. The manifest records
the font, CapCut and pixel sizes, flowers, motion parameters, layout and resource hashes.

On reburn, unchanged cues keep their decisions and only timing is re-validated. Changed text or cue
count sets `needs_design_review: true`: review the spans, durations and layout, update `cues` and
remove the flag before packaging. There are no persistent PNG/WebP previews; judge the actual
packaged video.
