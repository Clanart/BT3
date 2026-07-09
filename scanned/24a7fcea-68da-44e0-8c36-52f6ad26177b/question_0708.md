# Q708: NEAR required_balance_for_fin_transfer storage quote underestimates live state

## Question
Can an unprivileged attacker reach `internal accounting helper reached from public finalize paths` and make `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` reserve less storage than the live bridge state actually consumes because of computes how much storage balance a finalized transfer consumes on Near, violating `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Target helper functions that quote storage for pending transfers, finalization records, fast transfers, binds, or deployments.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Measure storage usage across maximal inputs and assert that quoted requirements always exceed or equal the true post-state footprint.
