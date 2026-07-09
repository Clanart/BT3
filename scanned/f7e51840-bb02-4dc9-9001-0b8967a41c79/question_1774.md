# Q1774: NEAR promise bookkeeping resume-path replay or duplication through cross-module drift

## Question
Can an unprivileged attacker use `public yield-resume flow through deferred outbound transfers` with control over message-storage account id, yielded promise id, repeated funding, and callback timing and desynchronize `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `resume-path replay or duplication` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives, violating `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` and the adjacent replay-protection bookkeeping after every branch.
