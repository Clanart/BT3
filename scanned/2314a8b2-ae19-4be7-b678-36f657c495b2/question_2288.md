# Q2288: NEAR init_transfer resume path resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `yield-resume callback for a previously deferred outbound transfer` with control over timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents and desynchronize `near/omni-bridge/src/lib.rs::init_transfer_resume` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because removes a stored promise id, retries storage transfer from the message account, and then restarts `init_transfer_internal` after a timeout or callback result, violating `resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::init_transfer_resume`
- Entrypoint: `yield-resume callback for a previously deferred outbound transfer`
- Attacker controls: timing of storage funding, promise completion order, storage owner, message-storage account state, and the original transfer message contents
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: resume logic must never let one deferred transfer be funded, resumed, or emitted more than once or under a different payer/value pairing
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::init_transfer_resume` and the adjacent replay-protection bookkeeping after every branch.
