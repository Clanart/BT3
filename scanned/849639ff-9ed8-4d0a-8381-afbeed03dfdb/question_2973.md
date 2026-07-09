# Q2973: NEAR omni-types prover args/results optional-field encoding ambiguity through cross-module drift

## Question
Can an unprivileged attacker use `public proof-submission flows across all chains` with control over serialized prover args bytes, proof kind tags, and typed result conversions and desynchronize `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` from the adjacent proof parsing and source authentication that shares the same asset, nonce, proof subject, or mapping specifically in the `optional-field encoding ambiguity` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Focus on drift between this module and the adjacent proof parsing and source authentication.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Also assert cross-module consistency between `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` and the adjacent proof parsing and source authentication after every branch.
