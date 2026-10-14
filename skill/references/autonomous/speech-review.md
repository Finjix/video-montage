# Agent speech review (`agent-speech-review/v1`)

ASR locates speech and drafts text; it never decides what was said. The agent reviews the full source
context, raw ASR, actual audio range, picture and verified asset copy, and corrects misrecognitions,
omissions, hallucinations and timings. The program checks scope, timing, files and hashes only; a
passing check does not mean a machine proved the wording. Never claim human listening, forced
alignment or an independent reviewer, and never insert ad copy, plot or product claims the speaker
did not say.

Reviews happen at three stages, each against its own audio:

| Stage | Command | Audio | Times |
| --- | --- | --- | --- |
| source | `source-speech-evidence` → `source-speech-review --review` | full source, mono 16 kHz WAV | source-local seconds |
| candidate | inside `approve-plan` (`segments[].speech_review`) | candidate PCM from plan evidence | candidate-local |
| clean | `clean-qc` → `clean-speech-review --review` | each rendered shot | shot-local |

Raw ASR (including empty results) is never overwritten; reviewed text and word times are stored
separately and bound by hash. Plans use the reviewed speech: each segment's text must equal the
complete reviewed words inside its native frame range (no word cut off, no quoting the same words from
elsewhere). If the real candidate speech differs from the plan, fix the plan and rerun `plan-evidence`;
the review cannot replace narrative content.

## Review file

```json
{
  "schema": "video-montage-agent-speech-review/v1",
  "stage": "source",
  "reviewer_role": "agent",
  "evidence_sha256": "<sha256 of the evidence file printed by the evidence command>",
  "asset_copy_sha256": "<sha256 of the accepted asset copy review>",
  "text_library_sha256": "<sha256 of the job text library snapshot>",
  "results": [{
    "source_sha256": "<original source sha256>",
    "audio_sha256": "<evidence WAV sha256>",
    "observed_text": "欢迎无尽东日",
    "speech_pass": true,
    "reason": "<context check and conclusion on every ASR difference>",
    "timing_reason": "<basis for the word times and any adjustment>",
    "reviewed_asr": {"text": "欢迎无尽冬日", "segments": [
      {"text": "欢迎无尽冬日", "start": 0.1, "end": 1.8,
       "words": [{"word": "欢迎", "start": 0.1, "end": 0.5}, {"word": "无尽冬日", "start": 0.5, "end": 1.8}]}]},
    "corrections": [{
      "before_start": 4, "before_end": 5, "after_start": 4, "after_end": 5, "before": "东", "after": "冬",
      "start_ms": 500, "end_ms": 1800, "reason": "<why the audio and context indicate this word>",
      "evidence": [{"kind": "text_library", "sha256": "<snapshot sha256>", "term_id": "game:wu-jin-dong-ri", "text": "无尽冬日"},
                   {"kind": "source_audio", "sha256": "<this WAV sha256>", "start_ms": 500, "end_ms": 1800}]}]
  }]
}
```

- **Source stage:** `results` covers every source exactly once.
- **Candidate stage:** each plan-review segment carries this finding as `speech_review` without
  `source_sha256`; `audio_sha256` is the candidate `pcm.sha256`, `observed_text` equals the candidate
  `asr.text` exactly, and the reviewed text must equal the planned segment text. The plan review
  header adds `speech_review_policy` and `text_library_sha256`.
- **Clean stage:** `"stage": "clean"` and
  `"results": [{"plan_id": "P1", "shots": [{"shot_index": 1, ...finding fields}]}]`, covering every
  output and every rendered shot once. Audio, raw ASR and expected text come from the evidence
  `observations`; words stay within their shot. A rejected review file can be corrected and resubmitted
  against the same evidence.

## Rules for a finding

- Text comparisons are normalized: punctuation and spaces removed, case folded, and 1–2 digit
  numbers read as values (30 = 三十, 13 = 十三, never 一三).
- `observed_text` is the raw ASR text exactly. `speech_pass: true` means the agent accepts this
  wording for the task (possibly with recorded inferences), not that every word was verified.
- `reviewed_asr`: full text = concatenated segment texts = concatenated words (after normalization).
  Word times increase, stay inside their segment and inside the audio. Use empty text and segments
  only when the source truly has no speech; still record each removed hallucination.
- `corrections` lists every normalized change, in order, exactly as computed by
  `speech_review.changes(observed, reviewed, autonomous_montage.normalized)` (0-based, end-exclusive
  character indexes; `SequenceMatcher(autojunk=False)`). Use an empty list when nothing changed.
  Each correction needs integer `start_ms`/`end_ms` inside the audio that cover its reviewed words
  (±250 ms), a concrete `reason`, and evidence:
  - `{"kind": "source_asr" | "source_speech" | "asset_copy", "sha256": ..., "text": "<quote>"}` quoting
    the bound text (`source_speech` only at candidate and clean stages);
  - `{"kind": "source_audio", "sha256": "<this WAV>", "start_ms": ..., "end_ms": ...}` with the same
    range as the correction, for empty recognition or hallucinations;
  - `{"kind": "text_library", ...}` for every fixed term the change touches; it always needs a
    `source_audio` item too ([text library](text-library.md)).

Compute the change list:

```text
assets/dependencies/python/python.exe -c "import json,sys;sys.path.insert(0,'scripts/autonomous');import autonomous_montage as a;print(json.dumps(a.SPEECH.changes(sys.argv[1],sys.argv[2],a.normalized)))" "<observed>" "<reviewed>"
```

## Inference when speech is uncertain

When wording cannot be fully confirmed, the current agent decides; no second agent or user
confirmation is needed, and ASR is not rerun hoping for a matching result. Choose the most likely
wording from the full ASR, segment recognitions, adjacent dialogue, scene and fixed spellings, and
write it into `reviewed_asr`.

- A changed character records `"inferred": true` on its correction, a `reason` that starts with
  "Inferred:" and explains the original recognition, the context and the choice, and `source_audio`
  evidence for the range (optionally plus ASR or asset quotes).
- When the inference keeps the raw ASR unchanged, keep `corrections` empty and state the doubtful
  words, audio range and basis in `reason`; explain estimated word times in `timing_reason`.
- Candidate and clean reviews cite the source inference and never upgrade it to "verified".
  The delivery summary lists inferred wording and its basis.

Speech inference never overrides technical failures: frame bounds, clipping, cuts, semantics, cast,
subtitles and batch checks still apply. Ambiguous cuts are fixed by moving native bounds, audio
cleanup or replacement footage.
