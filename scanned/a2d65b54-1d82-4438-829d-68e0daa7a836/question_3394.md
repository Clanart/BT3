# Q3394: NEAR omni-types prover args/results shared proof response reused across entrypoints via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission flows across all chains` and then replay or reorder later fee-claim proof submission so that `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared proof response reused across entrypoints` under defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Attack systems where one verifier contract serves deploy, finalize, metadata, and fee-claim flows with a shared result type. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Attempt to route accepted verifier outputs into every public proof consumer and assert that each entrypoint only accepts its intended result variant and source semantics. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
