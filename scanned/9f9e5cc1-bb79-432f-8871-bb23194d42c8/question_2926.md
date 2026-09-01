# Q2926: `is_test_vk` and non-canonical deserialization

## Question
Can a prover supply a non-canonical encoding accepted by the `BorshDeserialize` path feeding `is_test_vk` in `circuits-lib/src/bridge_circuit/constants.rs` (trailing bytes, an alternate encoding of the same value, an oversized length prefix) so the value the circuit hashes differs from the value the host and the on-chain script believe was committed?

## Target
- File/function: `circuits-lib/src/bridge_circuit/constants.rs` -> `is_test_vk` (This module contains constants used in the bridge circuit, including method IDs for different networks,)
- Entrypoint: attacker-serialized circuit input -> `is_test_vk`
- Attacker controls: the raw serialized bytes of the circuit input; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make the host's view and the circuit's view of the same input diverge
- Invariant to test: the bytes committed by the circuit decode to exactly one value, and re-encode identically
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: round-trip adversarial encodings and assert equality or rejection
