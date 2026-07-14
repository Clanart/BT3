# Q2528: new allow replay across contexts via synthetic key derivation inputs

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `new` in `crates/chia-puzzle-types/src/puzzles/nft.rs` with synthetic key derivation inputs with default-enabled consensus flags make chia_rs allow replay across contexts, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/nft.rs:54` / `new`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: synthetic key derivation inputs
- Exploit idea: Drive `new` through its public caller path using synthetic key derivation inputs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: round-trip puzzle args/solutions through CLVM and compare ownership fields.
