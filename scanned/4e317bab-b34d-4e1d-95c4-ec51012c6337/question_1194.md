# Q1194: `get_verifying_key` and the image/method id it verifies against

## Question
Can a prover reach `get_verifying_key` in `bridge-circuit-host/src/utils.rs` with a proof whose method id, image id or verification key corresponds to a different circuit version or network than the one the deployment expects - exploiting compile-time `option_env!` selection, a regtest bypass, or a method id read from the untrusted input - so a proof from a weaker circuit is accepted?

## Target
- File/function: `bridge-circuit-host/src/utils.rs` -> `get_verifying_key`
- Entrypoint: a proof generated against a different circuit build -> `get_verifying_key`
- Attacker controls: the method id and journal bytes carried in the proof input; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: substitute a proof from a circuit that does not enforce the deployment's rules
- Invariant to test: the image id verified against == the compile-time constant for the deployed network
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: assert `get_verifying_key` rejects a proof whose method id differs from the expected constant
