# Q1597: NEAR omni-types prover args/results proof kind or event class confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission flows across all chains` and then replay or reorder later fee-claim proof submission so that `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `proof kind or event class confusion` under defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Look for outer proof envelopes that carry a kind tag separately from the payload bytes they validate. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check every proof kind against every parser and assert that no valid envelope can downcast into the wrong bridge action. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
