# Q3056: NEAR add_fin_transfer rent compensation can leak reserve funds at boundary values

## Question
Can an unprivileged attacker trigger `internal finalization-state writer reached from public finalize flows` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_fin_transfer` violate `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event` in the `rent compensation can leak reserve funds` attack class because inserts a transfer id into `finalised_transfers` and charges storage for that finalized record becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Target reserve-compensation logic keyed by highest nonce or account initialization. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Drive sparse high-nonce patterns and assert that reserve accounting changes exactly match the actual storage objects created. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
