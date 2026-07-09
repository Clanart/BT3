# Q2757: NEAR finish_withdraw_v2 legacy or migration path aliasing via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `legacy/public withdrawal completion path on migrated tokens` and then replay or reorder the later settlement leg on another chain so that `near/omni-bridge/src/lib.rs::finish_withdraw_v2` ends up accepting two inconsistent interpretations of the same economic event specifically around `legacy or migration path aliasing` under accepts calls only from deployed-token contracts, allocates new origin and destination nonces, stores an outbound transfer, and logs an `InitTransfer` event to Ethereum, violating `legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::finish_withdraw_v2`
- Entrypoint: `legacy/public withdrawal completion path on migrated tokens`
- Attacker controls: sender id, bridged token contract calling in, amount, and recipient string parsed as Ethereum address
- Exploit idea: Use memo-triggered legacy paths, migrated-token aliases, or old/new token relationships to create a second valid outbound interpretation of the same balance change. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: legacy withdrawal completion must not let unprivileged callers mint outbound bridge claims without a corresponding inbound burn or with a misbound recipient
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Exercise both the modern and legacy branches with equivalent economic inputs and assert that only one bridge claim can arise from one unit of consumed value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
