# Q2679: NEAR omni-types prover args/results optional-field encoding ambiguity

## Question
Can an unprivileged attacker exploit empty-versus-present optional fields in proofs reaching `public proof-submission flows across all chains` so that `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` authenticates one payload but downstream logic interprets another because of defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior.
