# Delivering A + B + 尾板

## Pick the B segment and end card

Make one assignment per final MP4. The brief determines how many B variants each A gets, the split between UE,
narration and setting versions, and whether variants share the A's material number. Count these against the order
before encoding. A single packaged A can supply several assignments with the same subtitles, warning and BGM.

When supplied A files need packaging, compare each with the reference. Transcribe and time the actual dialogue,
check the subtitle style and bottom warning against the A layout, and mix the selected music under the original
sound. Burn the requested overlays and mix with FFmpeg to a separate master in the job folder. Review one master
per A before assigning its variants. If the brief asks for a different track per A, reuse that A's finished mix
across its B variants.

- Watch the end of the finished A and the first action of the proposed B. If A ends with 砍树, use an opening that
  shows chopping. If it promises food from fishing, fish should be visible in the opening. A general invitation to
  play leaves more room. Record the A line and the observed B action in the row's `match` field.
- Inspect the actual B picture and sound. A narration variant needs intelligible narration within the selected
  footage. UE footage can have narration too. For a complete B-only source use the full file. For a source that
  contains an earlier ad, inspect frames at both boundaries and use `b_range` to isolate B from the old A and tail.
- Choose a B and end card that fit the A layout, including landscape when supplied. Use the specified tail once,
  through its final frame. Check both cuts at normal speed and frame-step them for flashes, an old card or a
  missing first action. Check the A line and B opening together for meaning.

## Name the actual edit

Build each basename from the approved material ID, the order's labels, an A keyword and the specific B series.
Separate fields with one underscore. Summarize a line actually spoken in the finished A or a defining action in a
story A. Preserve explicit suffixes such as ZJJC when the brief uses them.

- For UE, including narrated UE, use the supplied 总表/细分 series. Look up the original B material ID first,
  then the same creative, then compare the observed gameplay with examples. Record the matching ID, example or
  reason in `series_basis` and flag approximate classifications in the handoff.
- For setting/narration assets using the four-series taxonomy, use picture and narration together. A fixed camp
  improving is **庇护所升级**. A vehicle or mobile base changing form is **载具升级**. A character's unfolding
  story is **剧情叙事**. An explanation of the setting or its rules is **世界观描述**.
- Past marketing orders numbered each final independently, e.g.
  `GOFCN69632_营销_高曙光AI_口播_钓鱼等待来一把_抵御二熊对抗.mp4`. A story order kept one number per A across
  four B variants, e.g. `GOFCN69686_AI剧情_设定3.0_冰湖漏勺救人ZJJC_剧情叙事.mp4`. In these formats the last
  field is the concrete series. Generic labels like UE系列 add no information. Take new numbers, batch labels,
  suffixes and variant counts from the current order. Resolve filename collisions before encoding.
- If the user requests a table update, take the material name from the verified final basename without `.mp4`.
  When the table calls for the final naming field as 提炼词, copy the last underscore-delimited field.

## Delivery config

Save a UTF-8 JSON assignment file under the job's config folder. Relative paths resolve from this file. Use
`expected_outputs` for the number of named finals. A celebrity job's `requested_outputs` counts packaged A sides.
This example delivers two B variants from one already packaged A:

```json
{
  "output_dir": "../../成片",
  "expected_outputs": 2,
  "endcard": "D:/assets/尾板-竖.mp4",
  "target_lufs": -12,
  "outputs": [
    {"plan_id": "P01", "b": "D:/assets/营地升级.mp4", "material_id": "GOFCN00001",
     "name_fields": ["营销", "口播"], "keyword": "建好第一间民居", "series": "庇护所升级",
     "match": "A ends on the first dwelling; B opens on the shelter being built."},
    {"plan_id": "P01", "b": "D:/assets/旧广告.mp4", "b_range": [90, 630], "material_id": "GOFCN00001",
     "name_fields": ["营销", "口播"], "keyword": "建好第一间民居", "series": "幸存者管理城建",
     "series_basis": "The original B ID matches the camp-management series in the supplied detail sheet."}
  ]
}
```

For independently supplied, ready A files use a separate working directory for `--job-dir`, put `"a":
"D:/assets/A1.mp4"` in each row in place of `plan_id`, and start at `deliver`. A packaging job resolves
each `plan_id` from its A manifest. Include every packaged A at least once. A row may override `endcard`,
e.g. for landscape. `b_range` uses zero-based frames in the original CFR B, with an exclusive end. Omit it
to play the whole B.

```powershell
& "<skill_root>/scripts/video_montage.cmd" deliver --job-dir "<job_dir>" --config "<delivery.json>" --check-only
& "<skill_root>/scripts/video_montage.cmd" deliver --job-dir "<job_dir>" --config "<delivery.json>"
```

## Sound and final checks

A's baked mix stops at its cut. B and the tail retain their own narration, effects, music and intentional silence.
The encoder matches each measurable segment to the order's integrated loudness, default **−12 LUFS**, and checks
the decoded AAC by segment and as a whole. A's intermediate packaging target is −18 LUFS. A single whole-file
reading can mask a quiet B or a loud end card. Review the three readings and true peaks, especially short tails
and transitions from speech to effects. Silent sections stay silent. Review any source flagged too short or
below the measurement gate, and check any unexpected missing track against the brief.

The encoder checks measurable sections within 1 LU of the target, the whole measurable program within 0.1 LU,
and a true peak ceiling of −1 dBTP. The result uses the A canvas at 60 fps, H.264 video and a continuous
48 kHz stereo AAC track. Audio samples are
aligned to each section's picture length. After encoding, inspect `reports/final-delivery.json` in the job folder
for the exact segment boundaries, measured LUFS and true peak, and the two picture seam comparisons. Verify the
named files and per-A variant counts against the order. The report records verified files incrementally and sets
`complete` after the full batch. Re-run the same command after a correction. It checks hashes and reuses valid
outputs. A naming revision retires the job's superseded files after the replacement batch passes.
