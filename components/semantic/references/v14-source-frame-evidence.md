# V14 native candidate-frame evidence contract

V14 retains the V13 decoded-frame evidence format and makes it mandatory in the V14 foreground phase chain.

Candidate evidence has four ordered states:

1. `SOURCE_ASR` records source-bound text and timings.
2. `CANDIDATE_PROPOSAL` creates non-eligible complete-utterance interval proposals.
3. `SOURCE_FRAME_EVIDENCE` runs `scripts/v14_source_frame_evidence.py` and writes hash-bound in/mid/out frames, requested timestamps, decoded PTS values and extraction status.
4. `CANDIDATE_BUILD` may promote only proposals with completed FFmpeg evidence whose source, proposal, profile and evidence hashes still match.

The extractor must be `ffmpeg_accurate_decode`. WPF thumbnails, UI screenshots, browser captures and synthetic images are not source-frame evidence. Equal pixel hashes are not automatically a fault because a source may contain a legitimate held shot.

Reject or keep pending when decoding fails, PTS exceeds tolerance, a path/hash binding changes, the visual scope includes a foreign person/full-screen gameplay/CTA outside the selected profile, or an in/out frame exposes a neighboring utterance or unfinished action.

This contract is the V14-specific entrypoint. [The V13 source-frame contract](v13-source-frame-evidence.md) remains the underlying compatible evidence lineage.
