# Q1854: NEAR required_balance_for_fin_transfer storage withdrawal escapes live liabilities at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public finalize paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` violate `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage` in the `storage withdrawal escapes live liabilities` attack class because computes how much storage balance a finalized transfer consumes on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Look for withdrawals and unregister paths that do not fully account for pending, finalized, or fast-transfer records. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Open live bridge state, withdraw aggressively, and assert that storage balances cannot fall below the reserved amount implied by that live state. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
