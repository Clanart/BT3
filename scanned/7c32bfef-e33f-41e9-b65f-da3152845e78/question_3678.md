# Q3678: NEAR promise bookkeeping storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public yield-resume flow through deferred outbound transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` violate `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer` in the `storage withdrawal escapes live liabilities` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
