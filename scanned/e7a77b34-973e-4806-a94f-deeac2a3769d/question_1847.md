# Q1847: NEAR add_fin_transfer storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `internal finalization-state writer reached from public finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fin_transfer` violate `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event` in the `storage quote underestimates live state` attack class because inserts a transfer id into `finalised_transfers` and charges storage for that finalized record becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
