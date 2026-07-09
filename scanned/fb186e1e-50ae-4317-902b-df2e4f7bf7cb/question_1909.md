# Q1909: Starknet nonce bitmap helper bitmap slot boundary corrupts replay protection at boundary values

## Question
Can an unprivileged attacker trigger `public nonce-tracking path through `fin_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` violate `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values` in the `bitmap slot boundary corrupts replay protection` attack class because packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
