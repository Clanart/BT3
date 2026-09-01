# Q5386: `journal_hash` trusts a prover-supplied index

## Question
Can a prover supply an index or length field consumed by `journal_hash` in `circuits-lib/src/bridge_circuit/mod.rs` (`payout_input_index`, `storage_proof.index`, watchtower index, MMR subroot index) that is out of range, wraps under `u32` arithmetic, or selects a different element than the protocol intends, so the circuit's committed journal describes a different fact than the one it verified?

## Target
- File/function: `circuits-lib/src/bridge_circuit/mod.rs` -> `journal_hash` (This module implements the Bridge Circuit for Clementine protocol)
- Entrypoint: a Groth16 proof produced for the bridge circuit from attacker-shaped inputs -> `journal_hash`
- Attacker controls: every field of the circuit input struct, including all indices and lengths; attacker is an unprivileged party who can broadcast Bitcoin transactions and craft circuit inputs; holds no protocol role or key
- Exploit idea: make the circuit bind its assertions to the wrong element and still commit a valid-looking journal
- Invariant to test: every index consumed by `journal_hash` is bounded by, and uniquely determined by, the protocol data it indexes
- Expected Immunefi impact: Critical - direct theft of bridged BTC via a forged inclusion/state proof accepted by the bridge circuit
- Fast validation: call `journal_hash` directly with boundary and wrapping indices and assert it rejects rather than commits
