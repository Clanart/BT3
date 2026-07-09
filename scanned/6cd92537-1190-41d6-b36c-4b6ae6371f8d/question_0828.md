# Q828: NEAR public proof-kind multiplexing final settlement and later fee claim can diverge

## Question
Can an unprivileged attacker drive `public proof-submission entrypoints` so that `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` settles principal under one interpretation of amount or transfer id while fee claim later uses another because of multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event.
