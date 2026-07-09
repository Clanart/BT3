# Q1648: NEAR public proof-kind multiplexing proof kind or event class confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission entrypoints` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs` ends up accepting two inconsistent interpretations of the same economic event specifically around `proof kind or event class confusion` under multiple public bridge entrypoints trust the same verifier envelope and then downcast to different `ProverResult` variants, violating `one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::fin_transfer/claim_fee/deploy_token/bind_token using shared verifier outputs`
- Entrypoint: `public proof-submission entrypoints`
- Attacker controls: proof kind, source chain, and bytes that a prover returns as `ProverResult`
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one validated proof must not be reusable across public bridge entrypoints whose economic effects differ, even if they share the same outer verifier contract
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
