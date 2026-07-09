# Q2531: NEAR omni-types prover args/results parser boundary or offset manipulation at boundary values

## Question
Can an unprivileged attacker trigger `public proof-submission flows across all chains` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` violate `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event` in the `parser boundary or offset manipulation` attack class because defines the serialized argument and result envelope that all proof-consuming public bridge flows trust becomes fragile at those edges?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Attack offset arithmetic, length prefixes, bucket indices, or body slicing in proof decoders. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Light client verification bypass
- Fast validation: Fuzz minimal, maximal, and malformed lengths and assert that every accepted proof round-trips into exactly the intended structured fields. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
