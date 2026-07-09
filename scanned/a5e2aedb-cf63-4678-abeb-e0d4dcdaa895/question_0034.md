# Q34: NEAR storage_withdraw storage quote underestimates live state

## Question
Can an unprivileged attacker reach `public NEAR storage-management entrypoint` and make `near/omni-bridge/src/storage.rs::storage_withdraw` reserve less storage than the live bridge state actually consumes because of subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
