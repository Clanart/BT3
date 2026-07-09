# Q1097: Starknet nonce bitmap helper replay state keyed too narrowly for the true domain through cross-module drift

## Question
Can an unprivileged attacker use `public nonce-tracking path through `fin_transfer`` with control over destination nonce values across slot boundaries and extremal `u64` inputs and desynchronize `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay state keyed too narrowly for the true domain` attack class because packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` and the adjacent replay-protection bookkeeping after every branch.
