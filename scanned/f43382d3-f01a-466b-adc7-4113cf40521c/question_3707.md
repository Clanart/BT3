# Q3707: NEAR public proof-kind multiplexing shared proof response reused across entrypoints at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` violate `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract` in the `shared proof response reused across entrypoints` attack class because multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
