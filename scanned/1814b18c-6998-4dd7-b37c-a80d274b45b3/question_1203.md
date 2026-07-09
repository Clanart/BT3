# Q1203: NEAR storage_withdraw storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_withdraw` violate `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds` in the `storage withdrawal escapes live liabilities` attack class because subtracts from stored storage balance and transfers NEAR back to the caller becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
