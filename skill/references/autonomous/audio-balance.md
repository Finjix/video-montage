# Segment loudness balance (`segment-loudness-balance/v1`)

Enabled by default for new clean renders, both in the autonomous workflow and when
`portable_frame_renderer.py` is used directly. Packaging, the final 1.2x and subtitle reburns reuse the
balanced clean track; existing MP4s and completed jobs are not rewritten (a full clean re-render is
needed to apply it).

After the source-frame cut, reviewed cleanup, segment speed, channel unification and duration lock, the
renderer measures each render segment's EBU R128 integrated loudness and true peak with FFmpeg
`loudnorm` (measurement only) and applies a static `volume` gain per segment. Natural dynamics, pitch
and cleaned silences are preserved and no compression is added. Enabled balancing converts to stereo so
measurement and encoding agree.

Defaults: target **-18 LUFS**, true-peak ceiling **-3 dBTP**, maximum boost **12 dB**. The common
target is the lowest of the requested target, each measurable segment's loudness plus the maximum
boost, and each segment's loudness plus its peak headroom, so weak or very peaky material lowers the
whole target instead of being over-amplified or clipped. Segments that are too short to measure, silent
or below -50 LUFS are never boosted and do not set the target (they may still be attenuated to respect
the peak ceiling); their status is recorded and they are not claimed to reach the target. Coalesced
adjacent ranges are measured as one render segment.

`audio_gain_db` (-24..0 dB, default 0) still applies after balancing as an extra manual attenuation; it
may keep intentional differences but cannot undo the peak limit.

## Configuration

Set on the plan root (or a frame plan root) and override per field in `outputs[]`:

```json
{"audio_balance": {"enabled": true, "target_lufs": -18, "true_peak_db": -3, "max_boost_db": 12}}
```

Omit to use defaults; `{"enabled": false}` disables balancing. Ranges: `target_lufs` -30..-10,
`true_peak_db` -9..-1, `max_boost_db` 0..18; non-finite numbers, wrong types and unknown fields are
rejected.

## Encoded verification

The render evidence records the configuration, common target, and per segment the input loudness and
true peak, automatic gain, total gain and the post-encode re-measurement of the temporary MP4 over the
actual output frames: a measurable segment must land within 0.8 LU of its expected loudness and stay
within the peak ceiling plus 0.5 dB (AAC tolerance). A failed measurement rejects the render, removes
the temporary encode and keeps any existing delivery. `clean-qc` keeps this evidence and requires it
to pass; clipping, cut, ASR, residual and final mix checks still apply, and BGM is mixed afterwards.
