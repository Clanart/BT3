# Q5538: `calculate_sha256` and what the committed journal actually binds

## Question
Can a prover vary a protocol-relevant value that `calculate_sha256` in `circuits-lib/src/common/hashes.rs` never folds into its committed output (an unbound field of `the module's input struct`, an index, a key ordering), so two different real-world situations produce the same journal and a proof for one settles the other?

## Target
- File/function: `circuits-lib/src/common/hashes.rs` -> `calculate_sha256` (Common hashing functions used in the Clementine protocol)
- Entrypoint: a Groth16 proof reused across situations -> `calculate_sha256`
- Attacker controls: the fields the attacker can vary without changing the journal; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: reuse one proof to settle a different withdrawal or vault
- Invariant to test: the committed journal is injective over (vault, withdrawal, payout, chain state)
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: vary each unbound field and assert the journal changes
