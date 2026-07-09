# Q1748: Starknet nonce bitmap helper bitmap slot boundary corrupts replay protection through cross-module drift

## Question
Can an unprivileged attacker use `public nonce-tracking path through `fin_transfer`` with control over destination nonce values across slot boundaries and extremal `u64` inputs and desynchronize `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `bitmap slot boundary corrupts replay protection` attack class because packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` and the adjacent replay-protection bookkeeping after every branch.
