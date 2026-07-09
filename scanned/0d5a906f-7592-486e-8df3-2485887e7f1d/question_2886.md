# Q2886: NEAR init_transfer resume path callback refund creates value gap through cross-module drift

## Question
Can an unprivileged attacker use `yield-resume callback for a previously deferred outbound transfer` with control over timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents and desynchronize `near/omni-bridge/src/lib.rs::init_transfer_resume` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `callback refund creates value gap` attack class because removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Target `ft_transfer_call`-style paths where refund semantics affect whether state is removed or custody is burned. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate every callback result and assert that no branch leaves both user-accessible funds and a still-live bridge claim for the same transfer. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer_resume` and the adjacent replay-protection bookkeeping after every branch.
