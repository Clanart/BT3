# Q3203: NEAR add_fin_transfer bitmap slot boundary corrupts replay protection

## Question
Can an unprivileged attacker use `internal finalization-state writer reached from public finalize flows` with boundary nonce values so that `near/omni-bridge/src/lib.rs::add_fin_transfer` aliases or mis-marks neighboring Starknet nonces because of inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Probe nonces around `250/251/252`, zero, and max `u64` values in the Starknet bitmap scheme.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Set and query boundary nonces and assert that each write flips exactly one intended replay bit.
