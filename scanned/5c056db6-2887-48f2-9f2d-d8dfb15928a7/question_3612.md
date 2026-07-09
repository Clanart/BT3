# Q3612: NEAR storage_withdraw callback interprets missing storage check as success at boundary values

## Question
Can an unprivileged attacker trigger `public NEAR storage-management entrypoint` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::storage_withdraw` violate `withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds` in the `callback interprets missing storage check as success` attack class because subtracts from stored storage balance and transfers NEAR back to the caller becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::storage_withdraw`
- Entrypoint: `public NEAR storage-management entrypoint`
- Attacker controls: withdraw amount, caller account, and timing relative to pending transfer lifecycle
- Exploit idea: Look for callbacks that inspect result arrays by index or default behavior rather than explicit success semantics. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: withdrawals must not let users pull out storage still required for pending, finalised, or fast-transfer records that underpin live funds
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Return malformed or missing callback data and assert that the bridge aborts safely before finalization or payout. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
