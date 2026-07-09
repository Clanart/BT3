# Q1935: NEAR promise bookkeeping resume-path replay or duplication at boundary values

## Question
Can an unprivileged attacker trigger `public yield-resume flow through deferred outbound transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` violate `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer` in the `resume-path replay or duplication` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
