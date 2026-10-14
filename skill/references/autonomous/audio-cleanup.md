# Spoken audio cleanup (`spoken-audio-cleanup/v1`)

`init` writes `audio_cleanup_policy: "spoken-audio-cleanup/v1"` into the work order and state; the plan
root and every segment carry the same field. When the source sound or word boundaries are unclear,
gather more evidence or repair; never claim human listening or forced alignment.

## Evidence and planning

`plan-evidence` extends each selected range by one second on both sides, decodes 48 kHz PCM from the
original, runs the bundled Silero VAD on the strongest channel (20 ms windows, 10 ms hop) and writes
`context.wav`, `waveform.png`, energy/VAD values, the surrounding word-level transcript, the selected
`target_words` and suspect `candidates`. Source hash, frame range, transcript digest, model hash,
parameters and file bytes are all bound; identical source/range/text/gain/policy/transcript reuse the
scan.

Neither audible energy nor missing ASR text authorizes a mute, and VAD can mistake a neighbouring
sentence's residual syllable for speech. Codex reads the context and waveform and classifies every
suspect: previous/next-sentence residue, non-speech noise, natural expression, valid speech or
uncertain. Breaths, laughter, exclamations and weak consonants are kept by default. Head/tail energy,
bursts and clipping are recorded; with cleanup they move to this review, but the rendered clean video
still has to pass the cut/clipping checks and the mute re-check.

Segments with the cleanup marker are never merged with adjacent ranges, so review, shot timeline and
render groups match. They must not overlap; for one long continuous shot select the whole continuous
range instead of repeating frames or splitting a sentence.

## Review fields

The plan review adds top-level `audio_cleanup_policy`, and every `segments[]` finding adds
`audio_cleanup`. Segments without suspects still confirm the protected word ranges (`decisions` may be
empty). The numbers below only illustrate the interface:

```json
{
  "policy": "spoken-audio-cleanup/v1",
  "evidence_digest": "<audio_cleanup.evidence_digest of this segment>",
  "natural_expression_preserved": true,
  "protected_speech_ranges": [{"word_index": 0, "text": "口播", "start_sample": 24000, "end_sample": 38400}],
  "decisions": [{"candidate_id": "event-001", "classification": "previous_next_speech", "action": "mute",
                 "start_sample": 4800, "end_sample": 9600,
                 "reason": "上下文与波形显示为上一句残音，其后低谷与本句分开"}]
}
```

- Sample positions are absolute 48 kHz positions in the decoded original, half-open
  `[start_sample, end_sample)`.
- `protected_speech_ranges` lists every `target_words` item in order with matching `word_index` and
  `text`, inside the audio of the selected frames; the default is the word range intersected with the
  segment. To correct a rough timestamp add a concrete `boundary_reason` with the matching waveform dip;
  one adjustment moves at most 750 ms and must still overlap the original word range.
- `classification` ∈ `previous_next_speech`, `non_speech_noise`, `natural_expression`, `valid_speech`,
  `uncertain`. Only the first two may `mute`; the others must `retain`, and `uncertain` fails the review.
  Every suspect gets a decision. A mute lies inside its suspect interval, does not overlap another mute,
  is longer than its 10 ms total fade, and never touches a protected range.

`approve-plan` compiles a separate, hash-bound `audio_cleanup_authorization`. A plan cannot carry its
own `audio_cleanup` or `audio_mute_ranges`. Rendering recompiles the review and rejects stale evidence,
missed suspects, changed parameters or replaced evidence bytes.

## Render, checks and reburn

The renderer resamples, cuts by source samples and mutes approved intervals on all channels with 5 ms
fades inside each interval. Picture frames, duration and subtitle timing are unchanged; gain, BGM mixing
and the single 1.2x follow.

`clean-qc` verifies the render used the authorization, that every channel stays ≤ -60 dBFS inside each
muted interval (excluding fades and about one AAC packet margin, scaled down for short intervals; an
interval with no checkable interior fails) and that boundaries have no jumps. `final-evidence` maps the
intervals through segment speed and the final 1.2x, rebuilds the same cleaned track plus authorized BGM
and requires the mix residual inside them to be ≤ -45 dBFS (the BGM itself is not muted).

Completion keeps `manifests/audio_cleanup_authorization.json` (intervals, protected ranges, provenance)
bound to the retained plan and clean inputs. Subtitle reburns reuse those checked tracks; new final
outputs are still re-checked. Changing cleanup needs a new review and render.
