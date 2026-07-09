# Q942: NEAR omni-types prover args/results final settlement and later fee claim can diverge via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public proof-submission flows across all chains` and then replay or reorder later fee-claim proof submission so that `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `final settlement and later fee claim can diverge` under defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
