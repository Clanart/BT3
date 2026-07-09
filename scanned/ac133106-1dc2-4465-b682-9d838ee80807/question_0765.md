# Q765: Starknet nonce bitmap helper replay state keyed too narrowly for the true domain

## Question
Can an unprivileged attacker exploit `public nonce-tracking path through `fin_transfer`` so that `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` treats two events from different chains, assets, or message classes as sharing one replay slot because of packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly.
