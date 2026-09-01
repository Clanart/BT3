# Q5296: `inner` and what the committed journal actually binds

## Question
Can a prover vary a protocol-relevant value that `inner` in `circuits-lib/src/bridge_circuit/structs.rs` never folds into its committed output (an unbound field of `WatchtowerInput`, an index, a key ordering), so two different real-world situations produce the same journal and a proof for one settles the other?

## Target
- File/function: `circuits-lib/src/bridge_circuit/structs.rs` -> `inner` (This module defines the data structures used in the Bridge Circuit)
- Entrypoint: a Groth16 proof reused across situations -> `inner`
- Attacker controls: the fields the attacker can vary without changing the journal; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: reuse one proof to settle a different withdrawal or vault
- Invariant to test: the committed journal is injective over (vault, withdrawal, payout, chain state)
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: vary each unbound field and assert the journal changes
