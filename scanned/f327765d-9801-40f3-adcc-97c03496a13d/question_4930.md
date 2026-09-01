# Q4930: `create_journal_digest` and the root a proof is checked against

## Question
Can a prover cause `create_journal_digest` in `circuits-lib/src/bridge_circuit/groth16_verifier.rs` to verify a proof against a state root that is attacker-influenced or unbound to the block the settlement is proved in - a root from the wrong L2 height, a root taken from the input rather than the verified journal - so bridge records are read from a state the chain never had?

## Target
- File/function: `circuits-lib/src/bridge_circuit/groth16_verifier.rs` -> `create_journal_digest` (This module implements the Groth16 verifier for the bridge circuit)
- Entrypoint: a proof with a substituted root -> `create_journal_digest`
- Attacker controls: the root value and journal bytes supplied to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: read fabricated bridge state as canonical
- Invariant to test: the state root used for storage verification == the root in the verified light-client journal for the payout's block
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert `create_journal_digest` fails when the root is not the one the verified journal carries
