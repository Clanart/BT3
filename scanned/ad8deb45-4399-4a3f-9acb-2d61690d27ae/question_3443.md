# Q3443: `receipt_from_inner` and non-canonical deserialization

## Question
Can a prover supply a non-canonical encoding accepted by the `BorshDeserialize` path feeding `receipt_from_inner` in `bridge-circuit-host/src/lib.rs` (trailing bytes, an alternate encoding of the same value, an oversized length prefix) so the value the circuit hashes differs from the value the host and the on-chain script believe was committed?

## Target
- File/function: `bridge-circuit-host/src/lib.rs` -> `receipt_from_inner`
- Entrypoint: attacker-serialized circuit input -> `receipt_from_inner`
- Attacker controls: the raw serialized bytes of the circuit input; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make the host's view and the circuit's view of the same input diverge
- Invariant to test: the bytes committed by the circuit decode to exactly one value, and re-encode identically
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: round-trip adversarial encodings and assert equality or rejection
