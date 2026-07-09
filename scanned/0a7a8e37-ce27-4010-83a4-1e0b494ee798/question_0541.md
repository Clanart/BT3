# Q541: NEAR required_balance_for_fin_transfer storage-preparation omission changes settlement meaning at boundary values

## Question
Can an unprivileged attacker trigger `internal accounting helper reached from public finalize paths` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` violate `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage` in the `storage-preparation omission changes settlement meaning` attack class because computes how much storage balance a finalized transfer consumes on Near becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
