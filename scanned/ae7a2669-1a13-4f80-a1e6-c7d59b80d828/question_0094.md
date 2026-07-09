# Q94: Starknet nonce bitmap helper replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public nonce-tracking path through `fin_transfer`` and make `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit` either bypass replay protection or consume it for the wrong event because of packs nonces into `(slot = nonce / 251, bit = pow2(nonce % 251))` before writing the bitmap, violating `bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_nonce_slot_and_bit`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce values across slot boundaries and extremal `u64` inputs
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: bitmap packing must mark exactly one nonce per settlement and must not alias, overflow, or skip boundary values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
