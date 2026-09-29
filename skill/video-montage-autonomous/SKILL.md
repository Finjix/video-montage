---
name: video-montage-autonomous
description: Run new Chinese spoken-video montage jobs with Codex semantic and visual review, ASR and PCM audio evidence, context-corrected subtitles, and no human or independent agent review.
metadata:
  version: "v260929"
---

# Autonomous video montage

Use this skill for **new** jobs. Read [the complete workflow](../../components/autonomous/README.md)
and run `components/autonomous/scripts/autonomous_montage.py` with
`dependencies/python/python.exe`. The work order must name original sources,
the asset pack, requested output count and a D-drive output root.

Codex itself examines source ASR, native decoded frames, the asset copy pack,
candidate evidence and final packaged frame evidence. Submit honest,
hash-bound `reviewer_role: codex` findings. Never claim an independent reviewer,
human listening, or forced word alignment. Machine gates re-run ASR on source,
candidate, clean and packaged media, inspect PCM, validate subtitles and
recheck output hashes. Reject ambiguous speech, visuals or copy rather than
guessing. After three rejected repair rounds, stop with the failure report.
For every candidate, inspect the first and last 13 native frames in order,
including frames around an original source transition. Report entry and exit
visual findings separately. Move a candidate boundary beyond any residual
transition only when a complete spoken phrase remains intact.

Do not migrate a job containing `three_suite_ff_state.json` or v260928 review
receipts. The original [video-montage skill](../video-montage/SKILL.md) and its
hash-bound work orders remain untouched for those jobs.
