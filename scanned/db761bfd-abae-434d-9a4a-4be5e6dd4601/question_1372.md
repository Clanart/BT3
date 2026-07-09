# Q1372: NEAR required_balance_for_fast_transfer storage withdrawal escapes live liabilities

## Question
Can an unprivileged attacker call `internal accounting helper reached from public fast-transfer paths` and make `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer` release storage funds that still back unresolved bridge state because of computes storage reserved for relayer-sponsored fast transfer state, violating `fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fast_transfer`
- Entrypoint: `internal accounting helper reached from public fast-transfer paths`
- Attacker controls: fast-transfer id structure, relayer fields, and destination branch
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records.
- Invariant to test: fast-transfer storage quoting must not let relayers create persistent claims that underpay for their own cleanup or refund paths
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state.
