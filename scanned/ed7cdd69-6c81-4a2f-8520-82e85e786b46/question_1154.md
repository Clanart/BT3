# Q1154: `verify_bridge_circuit` and what the committed journal actually binds

## Question
Can a prover vary a protocol-relevant value that `verify_bridge_circuit` in `bridge-circuit-host/src/structs.rs` never folds into its committed output (an unbound field of `BridgeCircuitBitvmInputs`, an index, a key ordering), so two different real-world situations produce the same journal and a proof for one settles the other?

## Target
- File/function: `bridge-circuit-host/src/structs.rs` -> `verify_bridge_circuit`
- Entrypoint: a Groth16 proof reused across situations -> `verify_bridge_circuit`
- Attacker controls: the fields the attacker can vary without changing the journal; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: reuse one proof to settle a different withdrawal or vault
- Invariant to test: the committed journal is injective over (vault, withdrawal, payout, chain state)
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: vary each unbound field and assert the journal changes
