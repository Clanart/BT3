# Q158: NEAR public proof-kind multiplexing recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public proof-submission entrypoints` that makes `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` settle principal to one party but authorize fee claim or callback routing for another due to multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
