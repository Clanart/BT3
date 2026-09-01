# Q1162: `calculate_succinct_output_prefix` constrains inputs but not the value that reaches the user

## Question
Can a prover satisfy every assertion `calculate_succinct_output_prefix` in `bridge-circuit-host/src/utils.rs` makes while the transaction it validates pays the withdrawer nothing - because outputs, amounts and destination scripts are never constrained - so a valid proof authorises a vault spend for a payout that never happened?

## Target
- File/function: `bridge-circuit-host/src/utils.rs` -> `calculate_succinct_output_prefix`
- Entrypoint: a Groth16 proof over an attacker-chosen transaction -> `calculate_succinct_output_prefix`
- Attacker controls: the full transaction body fed into the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove a settlement that transferred no value to the user
- Invariant to test: the transaction the circuit accepts pays the withdrawer the amount the withdrawal recorded
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct a zero-value payout, run `calculate_succinct_output_prefix`, and assert it panics instead of committing
