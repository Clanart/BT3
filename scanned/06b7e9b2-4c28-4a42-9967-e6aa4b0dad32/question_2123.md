# Q2123: NEAR public proof-kind multiplexing parser boundary or offset manipulation

## Question
Can an unprivileged attacker craft proof bytes for `public proof-submission entrypoints` that make `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` shift field boundaries, truncate payloads, or reinterpret trailing bytes because of multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields.
