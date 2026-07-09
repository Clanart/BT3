# Q3120: NEAR omni-types prover args/results optional-field encoding ambiguity at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission flows across all chains` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` violate `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event` in the `optional-field encoding ambiguity` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Focus on empty strings, optional fee recipients, and zero-length messages that are encoded specially. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Generate payloads that differ only by optional-field encoding and assert that they cannot verify under the same proof while producing different bridge behavior. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
