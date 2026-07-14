# Q2957: from json dict overflow or underflow a boundary check via newtype and enum field encodings

## Question
Can an unprivileged attacker round-trip macro-generated protocol objects targeting `from_json_dict` in `crates/chia_py_streamable_macro/src/lib.rs` with newtype and enum field encodings when equivalent-looking encodings are mixed make chia_rs overflow or underflow a boundary check, violating the invariant that macro-generated JSON and byte forms agree, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia_py_streamable_macro/src/lib.rs:530` / `from_json_dict`
- Entrypoint: round-trip macro-generated protocol objects
- Attacker controls: newtype and enum field encodings
- Exploit idea: Drive `from_json_dict` through its public caller path using newtype and enum field encodings; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: macro-generated JSON and byte forms agree
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: expand the macro on a representative struct and mutate each field in serialized bytes.
