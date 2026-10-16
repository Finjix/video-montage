# Segment loudness balance (`segment-loudness-balance/v1`)

Enabled by default for new clean renders. Packaging, the final 1.2x and subtitle reburns reuse the
balanced clean track; existing MP4s and completed jobs are not rewritten (a full clean re-render is
needed to apply it).

After the source-frame cut, reviewed cleanup, segment speed, channel unification and duration lock, the
renderer measures each render segment's EBU R128 integrated loudness and true peak with FFmpeg
`loudnorm` (measurement only) and applies a static `volume` gain per segment. Natural dynamics, pitch
and cleaned silences are preserved and no compression is added. Enabled balancing converts to stereo so
measurement and encoding agree.

Defaults: target **-23 LUFS** (the finished video measures about -23 LUFS with the BGM mixed in at its
automatic level; see [packaging](packaging.md)), true-peak ceiling **-3 dBTP**, maximum boost **12 dB**. The common
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
{"audio_balance": {"enabled": true, "target_lufs": -23, "true_peak_db": -3, "max_boost_db": 12}}
```

Omit to use defaults; `{"enabled": false}` disables balancing. Ranges: `target_lufs` -30..-10,
`true_peak_db` -9..-1, `max_boost_db` 0..18; non-finite numbers, wrong types and unknown fields are
rejected.

## Delivery loudness

The finished packaged video targets **-23 LUFS** with a tolerance of **±1 LU** (true peak at most
-1 dBTP, voice at least 6 dB over the BGM). `remaster-audio --delivery-dir <dir>` rebuilds the audio
of a finished packaged delivery to this rule without touching the picture: it scales the clean voice,
mixes the BGM at its automatic gain, measures the encoded result, corrects the voice gain once when it
is more than 0.5 LU off, and publishes only when every output passes (report:
`临时文件/reports/audio_remaster.json`). It does not issue a completion receipt, and the unpackaged
clean videos keep their old level.

## Encoded verification

The render evidence records the configuration, common target, and per segment the input loudness and
true peak, automatic gain, total gain and the post-encode re-measurement of the temporary MP4 over the
actual output frames: a measurable segment must land within 1 LU of its expected loudness and stay
within the peak ceiling plus 0.5 dB (AAC tolerance). A failed measurement rejects the render, removes
the temporary encode and keeps any existing delivery. `clean-qc` keeps this evidence and requires it
to pass; clipping, cut, ASR, residual and final mix checks still apply, and BGM is mixed afterwards (its gain is derived per video from the balanced voice).
