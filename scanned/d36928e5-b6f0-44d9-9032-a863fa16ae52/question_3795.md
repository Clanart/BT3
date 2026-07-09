# Q3795: NEAR omni-types prover args/results address normalization changes authenticated subject

## Question
Can an unprivileged attacker craft proof bytes for `public proof-submission flows across all chains` such that `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs` authenticates an address in one representation but later maps a normalized form to a different asset or account because of defines the serialized argument and result envelope that all proof-consuming public bridge flows trust, violating `proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event`?

## Target
- File/function: `near/omni-types/src/prover_args.rs and near/omni-types/src/prover_result.rs`
- Entrypoint: `public proof-submission flows across all chains`
- Attacker controls: serialized prover args bytes, proof kind tags, and typed result conversions
- Exploit idea: Target hex, byte-array, and account-id conversions between proof parsing and token/recipient lookup.
- Invariant to test: proof envelopes must not allow one chain’s verifier output to be reinterpreted as another chain’s settlement or deploy event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip every proof-derived address through all local conversions and assert that normalization never changes the bridge subject.
