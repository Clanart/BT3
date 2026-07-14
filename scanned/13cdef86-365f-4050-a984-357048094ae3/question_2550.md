# Q2550: SingletonStruct treat malformed data as a valid empty/default value via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `SingletonStruct` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when the same payload is parsed through public bindings make chia_rs treat malformed data as a valid empty/default value, violating the invariant that synthetic keys cannot be derived for unauthorized spends, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:38` / `SingletonStruct`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `SingletonStruct` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: synthetic keys cannot be derived for unauthorized spends
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
