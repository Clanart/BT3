# Q1426: Starknet nonce bitmap helper bitmap slot boundary corrupts replay protection

## Question
Can an unprivileged attacker use `public nonce-tracking path through `fin_transfer`` with boundary nonce values so that `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` aliases or mis-marks neighboring Starknet nonces because of packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit.
