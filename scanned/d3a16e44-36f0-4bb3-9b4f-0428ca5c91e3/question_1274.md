# Q1274: NEAR omni-types prover args/results final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission flows across all chains` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` violate `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event` in the `final settlement and later fee claim can diverge` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
