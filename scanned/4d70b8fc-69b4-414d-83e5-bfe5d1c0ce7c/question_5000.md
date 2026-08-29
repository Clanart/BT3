# Q5000: find-superset via liquidate: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `borrower`, any third-party principal reach `find-superset` (mainnet/contracts/registry/v0-egroup.clar:262) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:262` -> `find-superset`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `borrower`, any third-party principal
- Exploit idea: `find-superset` returns the FIRST mask that is a superset, walking buckets in population order rather than finding the tightest. Reach it through `liquidate` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with `borrower`, any third-party principal varied, and assert that the value `find-superset` returns is identical in both runs; a divergence confirms the finding.
