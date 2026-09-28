# V13 native candidate-frame evidence contract

Candidate evidence has four ordered states:

1. `SOURCE_ASR` records source-bound text and ASR timings.
2. `CANDIDATE_PROPOSAL` creates a non-eligible proposal index with complete-utterance source intervals.
3. `SOURCE_FRAME_EVIDENCE` uses `v13_source_frame_evidence.py` to write in/mid/out frame files, their SHA-256 hashes, requested timestamps and FFmpeg-decoded PTS values.
4. `CANDIDATE_BUILD` may approve only proposals whose source-frame evidence record has `status: completed` and `extractor: ffmpeg_accurate_decode`.

The extractor decodes through the requested timestamp and records the first selected decoded frame PTS. A PTS outside the configured tolerance is a failed extraction. A proposal with no completed evidence remains pending; it is not a rejected candidate and cannot be counted as available capacity.

WPF, UI thumbnails and screenshots are never native-frame evidence. Equal image hashes alone do not establish a capture fault because the source can hold a static frame. Frame validity depends on source and proposal hash bindings, successful FFmpeg decoding and decoded-PTS tolerance.
