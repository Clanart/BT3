# Q0386: `is_test_vk` constrains inputs but not the value that reaches the user

## Question
Can a prover satisfy every assertion `is_test_vk` in `circuits-lib/src/bridge_circuit/constants.rs` makes while the transaction it validates pays the withdrawer nothing - because outputs, amounts and destination scripts are never constrained - so a valid proof authorises a vault spend for a payout that never happened?

## Target
- File/function: `circuits-lib/src/bridge_circuit/constants.rs` -> `is_test_vk` (This module contains constants used in the bridge circuit, including method IDs for different networks,)
- Entrypoint: a Groth16 proof over an attacker-chosen transaction -> `is_test_vk`
- Attacker controls: the full transaction body fed into the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: prove a settlement that transferred no value to the user
- Invariant to test: the transaction the circuit accepts pays the withdrawer the amount the withdrawal recorded
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: construct a zero-value payout, run `is_test_vk`, and assert it panics instead of committing
