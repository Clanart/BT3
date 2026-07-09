# Q1108: NEAR omni-types prover args/results final settlement and later fee claim can diverge through cross-module drift

## Question
Can an unprivileged attacker use `public proof-submission flows across all chains` with control over serialized prover args bytes, proof kind tags, and typed result conversions and desynchronize `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `final settlement and later fee claim can diverge` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Also assert cross-module consistency between `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` and the adjacent proof parsing and source authentication after every branch.
