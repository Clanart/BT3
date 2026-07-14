# Q1338: from json dict reuse stale verification state via newtype and enum field encodings

## Question
Can an unprivileged attacker parse generated streamable bytes targeting `from_json_dict` in `crates/chia-traits/src/from_json_dict.rs` with newtype and enum field encodings at a fork-height or boundary-value activation point make chia_rs reuse stale verification state, violating the invariant that trusted parse mode is not exposed to attacker-controlled non-canonical bytes, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/from_json_dict.rs:105` / `from_json_dict`
- Entrypoint: parse generated streamable bytes
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `from_json_dict` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: trusted parse mode is not exposed to attacker-controlled non-canonical bytes
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
