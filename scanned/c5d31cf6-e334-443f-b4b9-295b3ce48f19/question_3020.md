# Q3020: NEAR public proof-kind multiplexing optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public proof-submission entrypoints` with control over proof kind, source chain, and bytes that a prover returns as `ProverResult` and desynchronize `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` and the adjacent proof parsing and source authentication after every branch.
