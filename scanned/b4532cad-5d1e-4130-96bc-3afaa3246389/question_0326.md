# Q326: NEAR public proof-kind multiplexing recipient or fee-recipient rebinding via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission entrypoints` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or fee-recipient rebinding` under multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
