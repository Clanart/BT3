# Q2464: NEAR add_fin_transfer storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal finalization-state writer reached from public finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fin_transfer` violate `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event` in the `storage withdrawal escapes live liabilities` attack class because inserts a transfer id into `finalised_transfers` and charges storage for that finalized record becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
