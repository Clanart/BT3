# Q2739: NEAR init_transfer resume path callback refund creates value gap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `yield-resume callback for a previously deferred outbound transfer` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::init_transfer_resume` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback refund creates value gap` under removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
