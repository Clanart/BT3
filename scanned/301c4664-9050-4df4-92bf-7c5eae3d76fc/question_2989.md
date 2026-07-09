# Q2989: NEAR promise bookkeeping storage quote underestimates live state through cross-module drift

## Question
Can an unprivileged attacker use `public yield-resume flow through deferred outbound transfers` with control over message-storage account id, yielded promise id, repeated funding, and callback timing and desynchronize `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `storage quote underestimates live state` attack class because tracks deferred init-transfer promises by account id so they can resume once storage arrives, violating `promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises`
- Entrypoint: `public yield-resume flow through deferred outbound transfers`
- Attacker controls: message-storage account id, yielded promise id, repeated funding, and callback timing
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: promise bookkeeping must never let an attacker orphan, overwrite, or resume someone else’s pending outbound transfer
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::add_promise/remove_promise/init_transfer_promises` and the adjacent replay-protection bookkeeping after every branch.
