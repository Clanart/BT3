# Q5162: `create_spv` and the root a proof is checked against

## Question
Can a prover cause `create_spv` in `bridge-circuit-host/src/bridge_circuit_host.rs` to verify a proof against a state root that is attacker-influenced or unbound to the block the settlement is proved in - a root from the wrong L2 height, a root taken from the input rather than the verified journal - so bridge records are read from a state the chain never had?

## Target
- File/function: `bridge-circuit-host/src/bridge_circuit_host.rs` -> `create_spv`
- Entrypoint: a proof with a substituted root -> `create_spv`
- Attacker controls: the root value and journal bytes supplied to the circuit; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: read fabricated bridge state as canonical
- Invariant to test: the state root used for storage verification == the root in the verified light-client journal for the payout's block
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert `create_spv` fails when the root is not the one the verified journal carries
