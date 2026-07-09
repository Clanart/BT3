# Q3207: NEAR storage_withdraw callback interprets missing storage check as success

## Question
Can an unprivileged attacker use `public NEAR storage-management entrypoint` to make `near/omni-bridge/src/storage.rs::storage_withdraw` misread asynchronous storage-check results because of subtracts from stored storage balance and transfers NEAR back to the caller, violating `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout.
