# Q1326: NEAR public proof-kind multiplexing final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission entrypoints` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` violate `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract` in the `final settlement and later fee claim can diverge` attack class because multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
