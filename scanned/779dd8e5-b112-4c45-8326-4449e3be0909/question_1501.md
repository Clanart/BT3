# Q1501: register for ph updates accept invalid consensus data via peer handshake addresses

## Question
Can an unprivileged attacker supply peer address and framing data targeting `register_for_ph_updates` in `crates/chia-client/src/peer.rs` with peer handshake addresses when values sit exactly at max/min integer boundaries make chia_rs accept invalid consensus data, violating the invariant that remote peers cannot make local code accept invalid protocol state, and realistically causing the in-scope impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption?

## Target
- File/function: `crates/chia-client/src/peer.rs:176` / `register_for_ph_updates`
- Entrypoint: supply peer address and framing data
- Attacker controls: peer handshake addresses
- Exploit idea: Drive `register_for_ph_updates` through its public caller path using peer handshake addresses; combine ordering, boundary, or alternate encoding cases until the checked state differs from the consensus/model expectation.
- Invariant to test: remote peers cannot make local code accept invalid protocol state
- Expected Immunefi impact: Critical. Valid unprivileged CLVM program, spend bundle, block, proof, or serialized network object can trigger deterministic consensus divergence, chain halt, or committed state corruption
- Fast validation: simulate malicious peer bytes and assert local parser rejects invalid state.
