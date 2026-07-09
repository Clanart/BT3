# Q95: Starknet set_transfer_finalised helper replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public nonce-tracking path through `fin_transfer`` and make `starknet/src/omni_bridge.cairo::_set_transfer_finalised` either bypass replay protection or consume it for the wrong event because of reads the bitmap word for a slot, ORs in the target bit, and writes it back to storage, violating `setting one nonce finalised must never mutate neighboring nonces or allow a partially-validated settlement to consume replay protection first`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_set_transfer_finalised`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce, bitmap slot contents, and repeated calls on the same or neighboring nonces
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: setting one nonce finalised must never mutate neighboring nonces or allow a partially-validated settlement to consume replay protection first
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
