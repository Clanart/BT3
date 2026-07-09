# Q1156: Starknet completed_transfers bitmap storage bitmap slot boundary corrupts replay protection through cross-module drift

## Question
Can an unprivileged attacker use `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised`` with control over destination nonce choice, neighboring nonces in the same slot, and repeated calls and desynchronize `starknet/src/omni_bridge.cairo::completed_transfers` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `bitmap slot boundary corrupts replay protection` attack class because stores 251 replay-protection bits per storage slot for Starknet inbound settlement, violating `bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::completed_transfers`
- Entrypoint: `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised``
- Attacker controls: destination nonce choice, neighboring nonces in the same slot, and repeated calls
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Also assert cross-module consistency between `starknet/src/omni_bridge.cairo::completed_transfers` and the adjacent replay-protection bookkeeping after every branch.
