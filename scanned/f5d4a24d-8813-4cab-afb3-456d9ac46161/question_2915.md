# Q2915: to json dict derive a different canonical hash via newtype and enum field encodings

## Question
Can an unprivileged attacker deserialize JSON dictionaries targeting `to_json_dict` in `crates/chia-traits/src/to_json_dict.rs` with newtype and enum field encodings when the attacker can choose ordering inside a batch make chia_rs derive a different canonical hash, violating the invariant that generated parse/stream/hash code covers all fields in order, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-traits/src/to_json_dict.rs:12` / `to_json_dict`
- Entrypoint: deserialize JSON dictionaries
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `to_json_dict` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: generated parse/stream/hash code covers all fields in order
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: assert trusted parse is only used after a canonical parse boundary.
