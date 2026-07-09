# Q2243: NEAR promise bookkeeping callback refund creates value gap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public yield-resume flow through deferred outbound transfers` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback refund creates value gap` under tracks deferred init-transfer promises by account id so they can resume once storage arrives, violating `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
