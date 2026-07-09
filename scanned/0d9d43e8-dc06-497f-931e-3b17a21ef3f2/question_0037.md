# Q37: NEAR required_balance_for_fin_transfer storage-preparation omission changes settlement meaning

## Question
Can an unprivileged attacker make `internal accounting helper reached from public finalize paths` omit or reorder required storage setup so that `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer` settles under a different assumption about who can receive principal or fees because of computes how much storage balance a finalized transfer consumes on Near, violating `storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage`?

## Target
- File/function: `near/omni-bridge/src/storage.rs::required_balance_for_fin_transfer`
- Entrypoint: `internal accounting helper reached from public finalize paths`
- Attacker controls: destination branch and fee/storage action structure
- Exploit idea: Target recipient or fee-recipient storage checks that gate token delivery or fee minting.
- Invariant to test: storage quoting must cover every finalization record so settlement cannot create liabilities larger than the prepaid storage
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Reorder or omit storage-deposit actions and assert that settlement either fully aborts before finalization or completes with all intended recipients properly provisioned.
