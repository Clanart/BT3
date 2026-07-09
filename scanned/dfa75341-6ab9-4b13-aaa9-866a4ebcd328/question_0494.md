# Q494: NEAR public proof-kind multiplexing recipient or fee-recipient rebinding through cross-module drift

## Question
Can an unprivileged attacker use `public proof-submission entrypoints` with control over proof kind, source chain, and bytes that a prover returns as `ProverResult` and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `recipient or fee-recipient rebinding` attack class because multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` and the adjacent proof parsing and source authentication after every branch.
