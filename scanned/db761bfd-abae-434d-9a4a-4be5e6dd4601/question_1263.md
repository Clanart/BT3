# Q1263: Starknet nonce bitmap helper replay state keyed too narrowly for the true domain at boundary values

## Question
Can an unprivileged attacker trigger `public nonce-tracking path through `fin_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` violate `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values` in the `replay state keyed too narrowly for the true domain` attack class because packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
