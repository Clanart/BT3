# Q2551: new mis-order operations across a batch via lineage proofs and launcher ids

## Question
Can an unprivileged attacker construct wallet puzzle data from attacker-controlled fields targeting `new` in `crates/chia-puzzle-types/src/puzzles/singleton.rs` with lineage proofs and launcher ids when the same payload is parsed through public bindings make chia_rs mis-order operations across a batch, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/singleton.rs:46` / `new`
- Entrypoint: construct wallet puzzle data from attacker-controlled fields
- Attacker controls: lineage proofs and launcher ids
- Exploit idea: Drive `new` through its public caller path using lineage proofs and launcher ids; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: fuzz synthetic key inputs and assert signature authorization is unchanged.
