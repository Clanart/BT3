# Q2615: NEAR add_fin_transfer rent compensation can leak reserve funds

## Question
Can an unprivileged attacker exploit `internal finalization-state writer reached from public finalize flows` so that `near/omni-bridge/src/lib.rs::add_fin_transfer` overpays or refunds reserve lamports/NEAR while still keeping the same replay-protection or storage state because of inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Target reserve-compensation logic keyed by highest nonce or account initialization.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Drive sparse high-nonce patterns and assert that reserve accounting changes exactly match the actual storage objects created.
