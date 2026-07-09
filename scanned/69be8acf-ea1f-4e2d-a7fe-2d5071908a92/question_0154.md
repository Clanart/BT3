# Q154: Starknet completed_transfers bitmap storage replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised`` and make `starknet/src/omni_bridge.cairo::completed_transfers` either bypass replay protection or consume it for the wrong event because of stores 251 replay-protection bits per storage slot for Starknet inbound settlement, violating `bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::completed_transfers`
- Entrypoint: `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised``
- Attacker controls: destination nonce choice, neighboring nonces in the same slot, and repeated calls
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
