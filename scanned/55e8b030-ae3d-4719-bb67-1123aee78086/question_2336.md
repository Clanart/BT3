# Q2336: remove-user-collateral via borrow: attach a price resolved for one asset to a different asset

## Question
Does `borrow` (mainnet/contracts/market/v0-4-market.clar:1238) let an unprivileged attacker who controls the `price-feeds` buffers reach `remove-user-collateral` (mainnet/contracts/market/v0-market-vault.clar:205) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it asserts sufficiency then `map-delete`s only on an exact zero, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:205` -> `remove-user-collateral`
- Entrypoint: `borrow` (`mainnet/contracts/market/v0-4-market.clar:1238`), unprivileged and publicly callable
- Attacker controls: the `price-feeds` buffers
- Exploit idea: `remove-user-collateral` asserts sufficiency then `map-delete`s only on an exact zero. Reach it through `borrow` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `borrow` twice with the `price-feeds` buffers varied, and assert that the value `remove-user-collateral` returns is identical in both runs; a divergence confirms the finding.
