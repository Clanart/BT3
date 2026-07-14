# Q983: CatArgs allow replay across contexts via royalty and settlement puzzle fields

## Question
Can an unprivileged attacker build synthetic keys and lineage proofs targeting `CatArgs` in `crates/chia-puzzle-types/src/puzzles/cat.rs` with royalty and settlement puzzle fields when equivalent-looking encodings are mixed make chia_rs allow replay across contexts, violating the invariant that offer/CAT/NFT/DID invariants preserve asset ownership, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-puzzle-types/src/puzzles/cat.rs:12` / `CatArgs`
- Entrypoint: build synthetic keys and lineage proofs
- Attacker controls: royalty and settlement puzzle fields
- Exploit idea: Drive `CatArgs` through its public caller path using royalty and settlement puzzle fields; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: offer/CAT/NFT/DID invariants preserve asset ownership
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: recompute puzzle tree hash from independent CLVM construction.
