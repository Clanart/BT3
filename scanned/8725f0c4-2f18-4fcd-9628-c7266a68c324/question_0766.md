# Q766: Starknet set_transfer_finalised helper bitmap slot boundary corrupts replay protection

## Question
Can an unprivileged attacker use `public nonce-tracking path through `fin_transfer`` with boundary nonce values so that `starknet/src/omni_bridge.cairo::_set_transfer_finalised` aliases or mis-marks neighboring Starknet nonces because of reads the bitmap word for a slot, ORs in the target bit, and writes it back to storage, violating `setting one nonce finalised must never mutate neighboring nonces or allow a partially-validated settlement to consume replay protection first`?

## Target
- File/function: `starknet/src/omni_bridge.cairo::_set_transfer_finalised`
- Entrypoint: `public nonce-tracking path through `fin_transfer``
- Attacker controls: destination nonce, bitmap slot contents, and repeated calls on the same or neighboring nonces
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme.
- Invariant to test: setting one nonce finalised must never mutate neighboring nonces or allow a partially-validated settlement to consume replay protection first
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit.
