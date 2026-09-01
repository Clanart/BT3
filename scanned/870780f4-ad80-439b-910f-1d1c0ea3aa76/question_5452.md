# Q5452: `is_test_vk` and what the committed journal actually binds

## Question
Can a prover vary a protocol-relevant value that `is_test_vk` in `circuits-lib/src/bridge_circuit/constants.rs` never folds into its committed output (an unbound field of `the module's input struct`, an index, a key ordering), so two different real-world situations produce the same journal and a proof for one settles the other?

## Target
- File/function: `circuits-lib/src/bridge_circuit/constants.rs` -> `is_test_vk` (This module contains constants used in the bridge circuit, including method IDs for different networks,)
- Entrypoint: a Groth16 proof reused across situations -> `is_test_vk`
- Attacker controls: the fields the attacker can vary without changing the journal; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: reuse one proof to settle a different withdrawal or vault
- Invariant to test: the committed journal is injective over (vault, withdrawal, payout, chain state)
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: vary each unbound field and assert the journal changes
