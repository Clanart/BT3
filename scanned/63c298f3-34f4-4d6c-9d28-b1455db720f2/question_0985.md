# Q985: curry tree hash accept invalid consensus data via CAT/NFT/DID/offer/singleton puzzle arguments

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `curry_tree_hash` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with CAT/NFT/DID/offer/singleton puzzle arguments when equivalent-looking encodings are mixed make chia_rs accept invalid consensus data, violating the invariant that puzzle tree hashes bind all authorization fields, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:29` / `curry_tree_hash`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: CAT/NFT/DID/offer/singleton puzzle arguments
- Exploit idea: Drive `curry_tree_hash` through its public caller path using CAT/NFT/DID/offer/singleton puzzle arguments; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: puzzle tree hashes bind all authorization fields
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
