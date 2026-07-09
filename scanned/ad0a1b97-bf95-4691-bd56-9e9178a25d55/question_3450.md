# Q3450: NEAR init_transfer resume path same fee collectible twice through cross-module drift

## Question
Can an unprivileged attacker use `yield-resume callback for a previously deferred outbound transfer` with control over timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents and desynchronize `near/omni-bridge/src/lib.rs::init_transfer_resume` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `same fee collectible twice` attack class because removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer_resume` and the adjacent replay-protection bookkeeping after every branch.
