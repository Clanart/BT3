# Q2873: NEAR public proof-kind multiplexing optional-field encoding ambiguity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission entrypoints` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` ends up accepting two inconsistent interpretations of the same economic event specifically around `optional-field encoding ambiguity` under multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
