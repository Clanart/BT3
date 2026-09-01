# Q4013: `check_hash_valid` and the root a proof is checked against

## Question
Can a prover cause `check_hash_valid` in `circuits-lib/src/header_chain/mod.rs` to verify a proof against a state root that is attacker-influenced or unbound to the block the settlement is proved in - a root from the wrong L2 height, a root taken from the input rather than the verified journal - so bridge records are read from a state the chain never had?

## Target
- File/function: `circuits-lib/src/header_chain/mod.rs` -> `check_hash_valid` (This module contains the implementation of the header chain circuit, which is basically)
- Entrypoint: a proof with a substituted root -> `check_hash_valid`
- Attacker controls: the root value and journal bytes supplied to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: read fabricated bridge state as canonical
- Invariant to test: the state root used for storage verification == the root in the verified light-client journal for the payout's block
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert `check_hash_valid` fails when the root is not the one the verified journal carries
