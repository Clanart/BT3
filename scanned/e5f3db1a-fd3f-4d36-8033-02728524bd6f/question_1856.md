# Q1856: from parent allow replay across contexts via duplicate public-key/message pairs

## Question
Can an unprivileged attacker submit aggregate signature material targeting `from_parent` in `crates/chia-bls/src/signature.rs` with duplicate public-key/message pairs when equivalent-looking encodings are mixed make chia_rs allow replay across contexts, violating the invariant that cached pairing results cannot substitute different messages, and realistically causing the in-scope impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data?

## Target
- File/function: `crates/chia-bls/src/signature.rs:520` / `from_parent`
- Entrypoint: submit aggregate signature material
- Attacker controls: duplicate public-key/message pairs
- Exploit idea: Drive `from_parent` through its public caller path using duplicate public-key/message pairs; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: cached pairing results cannot substitute different messages
- Expected Immunefi impact: High. Streamable/CLVM/serde/Python/wasm boundary parsing bug causes non-canonical bytes, integer confusion, hash mismatch, or cross-language disagreement in consensus-critical data
- Fast validation: construct aggregate signature vectors with duplicates, infinity, and swapped messages.
