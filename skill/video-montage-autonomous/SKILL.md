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
the asset pack and requested output count. Deliver under the project work/ directory;
keep the job state, evidence, reviews and temporary media in the delivery 临时文件/ directory.

Codex itself examines source ASR, native decoded frames, the asset copy pack,
candidate evidence and final packaged frame evidence. Submit honest,
hash-bound `reviewer_role: codex` findings. Never claim an independent reviewer,
human listening, or forced word alignment. Machine gates re-run ASR on source,
candidate, clean and packaged media, inspect PCM, validate subtitles and
recheck output hashes. Reject ambiguous speech, visuals or copy rather than
guessing. After three rejected repair rounds, stop with the failure report unless
the user explicitly requests continued repair until complete delivery. Honor that
request with the `repair --continue-until-complete --authorization <user instruction>`
command, preserving all previous failures, evidence, and round numbers. Continue
repairing or rebuilding until every requested packaged video passes; never replace
quality checks with invented approvals or deliver partial results as completion.
Reuse the original delivery directory for every repair and reburn; do not create
new version folders. Render and check temporary video files before replacing
existing MP4s, update subtitles and manifests in place, and invalidate stale
completion receipts until the replacement batch passes all delivery checks.
For every candidate, inspect the first and last 30 native frames in order,
including the required first and last 13 frames and any original source
transition near them. Report entry and exit visual findings separately.
Compare the last frames of each outgoing segment with the first frames of the
incoming segment for subject position, scale, and shot size. If the cut makes
those change abruptly, choose a compatible cut or an explicit transition.
Inspect the rendered video frame by frame around every splice; two shot changes
in quick succession are a defect even when each candidate edge looks stable.
Move a candidate boundary beyond any residual transition only when a complete
spoken phrase remains intact.

Do not migrate a job containing `three_suite_ff_state.json` or v260928 review
receipts. The original [video-montage skill](../video-montage/SKILL.md) and its
hash-bound work orders remain untouched for those jobs.
