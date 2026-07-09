# Q3608: NEAR add_fin_transfer bitmap slot boundary corrupts replay protection at boundary values

## Question
Can an unprivileged attacker trigger `internal finalization-state writer reached from public finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fin_transfer` violate `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event` in the `bitmap slot boundary corrupts replay protection` attack class because inserts a transfer id into `finalised_transfers` and charges storage for that finalized record becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
