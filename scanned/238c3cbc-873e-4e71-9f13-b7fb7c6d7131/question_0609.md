# Q609: NEAR omni-types prover args/results recipient or fee-recipient rebinding at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission flows across all chains` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` violate `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event` in the `recipient or fee-recipient rebinding` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
