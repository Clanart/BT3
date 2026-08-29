# Q4424: accrue-user-collateral via deposit: apply a transform after the gate that was supposed to boun

## Question
Does `deposit` (mainnet/contracts/vault/v0-vault-stx.clar:763) let an unprivileged attacker who controls `recipient`, including a contract principal reach `accrue-user-collateral` (mainnet/contracts/market/v0-4-market.clar:270) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it accrues only rows that `is-ztoken` recognises, skipping everything else, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:270` -> `accrue-user-collateral`
- Entrypoint: `deposit` (`mainnet/contracts/vault/v0-vault-stx.clar:763`), unprivileged and publicly callable
- Attacker controls: `recipient`, including a contract principal
- Exploit idea: `accrue-user-collateral` accrues only rows that `is-ztoken` recognises, skipping everything else. Reach it through `deposit` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `deposit` twice with `recipient`, including a contract principal varied, and assert that the value `accrue-user-collateral` returns is identical in both runs; a divergence confirms the finding.
