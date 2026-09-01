# Q4558: `verify_proof` and prevout/utxo list construction

## Question
Can a prover supply a prevout or UTXO list to `verify_proof` in `circuits-lib/src/header_chain/mmr_native.rs` whose length or ordering does not match the transaction being validated - fewer prevouts than inputs, duplicated entries, or a list built from attacker-chosen txouts - so the sighash computed inside the circuit binds values that were never on chain?

## Target
- File/function: `circuits-lib/src/header_chain/mmr_native.rs` -> `verify_proof` (Full-featured MMR implementation for native (non-zkVM) environments)
- Entrypoint: an attacker-shaped circuit input -> `verify_proof`
- Attacker controls: the prevout list, its ordering and its length; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: compute an in-circuit sighash over fabricated prevouts
- Invariant to test: the prevout list used == the actual prevouts of the transaction's inputs, one per input, in order
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert `verify_proof` rejects prevout lists that do not match the input list
