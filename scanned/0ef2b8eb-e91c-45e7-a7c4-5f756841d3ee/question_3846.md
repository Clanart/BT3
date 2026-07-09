# Q3846: NEAR init_transfer resume path fee payout and storage refund overlap via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `yield-resume callback for a previously deferred outbound transfer` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::init_transfer_resume` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee payout and storage refund overlap` under removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Target callbacks that remove state and refund storage while also minting or transferring fees. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model every success/failure order and assert that one event cannot produce both the intended fee and an unintended storage rebate for the attacker. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
