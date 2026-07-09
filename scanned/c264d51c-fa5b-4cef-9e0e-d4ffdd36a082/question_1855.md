# Q1855: NEAR required_balance_for_fast_transfer storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public fast-transfer paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` violate `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths` in the `storage withdrawal escapes live liabilities` attack class because computes storage reserved for relayer-sponsored fast transfer state becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
