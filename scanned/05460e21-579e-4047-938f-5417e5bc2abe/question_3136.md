# Q3136: NEAR promise bookkeeping storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `public yield-resume flow through deferred outbound transfers` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` violate `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer` in the `storage quote underestimates live state` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
