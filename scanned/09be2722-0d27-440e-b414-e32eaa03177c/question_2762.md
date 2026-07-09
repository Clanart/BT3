# Q2762: NEAR add_fin_transfer rent compensation can leak reserve funds via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal finalization-state writer reached from public finalize flows` and then replay or reorder the earlier source-chain event or later forwarded bridge leg so that `near/omni-bridge/src/lib.rs::add_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `rent compensation can leak reserve funds` under inserts a transfer id into `finalised_transfers` and charges storage for that finalized record, violating `finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_fin_transfer`
- Entrypoint: `internal finalization-state writer reached from public finalize flows`
- Attacker controls: transfer id chosen from source event and the timing of repeat calls
- Exploit idea: Target reserve-compensation logic keyed by highest nonce or account initialization. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: finalization writes must be globally unique and must not be reverted or duplicated in ways that reopen the same bridge event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Drive sparse high-nonce patterns and assert that reserve accounting changes exactly match the actual storage objects created. Then replay or reorder the earlier source-chain event or later forwarded bridge leg and assert that the bridge still exposes only one valid economic outcome.
