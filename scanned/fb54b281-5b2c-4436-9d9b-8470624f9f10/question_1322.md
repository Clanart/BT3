# Q1322: Starknet completed_transfers bitmap storage bitmap slot boundary corrupts replay protection at boundary values

## Question
Can an unprivileged attacker trigger `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `starknet/src/omni_bridge.cairo::completed_transfers` violate `bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values` in the `bitmap slot boundary corrupts replay protection` attack class because stores 251 replay-protection bits per storage slot for Starknet inbound settlement becomes fragile at those edges?

## Target
- File/function: `starknet/src/omni_bridge.cairo::completed_transfers`
- Entrypoint: `public finalize path through `is_transfer_finalised` and `_set_transfer_finalised``
- Attacker controls: destination nonce choice, neighboring nonces in the same slot, and repeated calls
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: bitmap state must not alias or silently overwrite neighboring nonces under boundary or maximal values
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
