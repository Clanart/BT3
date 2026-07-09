# Q1206: NEAR required_balance_for_fin_transfer storage quote underestimates live state at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public finalize paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` violate `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage` in the `storage quote underestimates live state` attack class because computes how much storage balance a finalized transfer consumes on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
