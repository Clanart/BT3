# Q105: NEAR omni-types prover args/results recipient or fee-recipient rebinding

## Question
Can an unprivileged attacker submit data through `public proof-submission flows across all chains` that makes `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` settle principal to one party but authorize fee claim or callback routing for another due to defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple.
