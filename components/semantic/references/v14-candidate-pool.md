# V14 frozen candidate pool contract

Freeze only candidates with approved status, capacity eligibility and valid ASR, identity, semantic and FFmpeg-frame evidence hashes. Every source is bound by SHA-256 and processing stage; every pool is bound by the selected style-profile hash.

`plan` produces a delta source manifest containing only sources that are new, changed, unproven or governed by a changed profile. Reused candidates are observationally imported from the pool and must retain valid evidence paths and hashes. A missing or invalid evidence file moves its source to the delta manifest.

The candidate pool is not a source of new approvals. It never promotes a draft, changes a candidate, relaxes a gate or calls a model. A V14 run can use the plan to skip repeated ASR/frame/identity work for unchanged, fully evidenced sources.
