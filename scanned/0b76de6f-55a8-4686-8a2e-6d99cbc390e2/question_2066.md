# Q2066: filter-u128 via collateral-remove: apply a transform after the gate that was supposed to boun

## Question
Entering through `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) while controlling whether the position has any enabled debt row (the has-debt branch), can an unprivileged attacker make `filter-u128` (mainnet/contracts/registry/v0-egroup.clar:97) apply a transform after the gate that was supposed to bound its output? `filter-u128` filters a 128-entry bucket list, so the invariant that a position that holds value can always be priced, and therefore always closed would fail, yielding protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/registry/v0-egroup.clar:97` -> `filter-u128`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: whether the position has any enabled debt row (the has-debt branch)
- Exploit idea: `filter-u128` filters a 128-entry bucket list. Reach it through `collateral-remove` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-remove` twice with whether the position has any enabled debt row (the has-debt branch) varied, and assert that the value `filter-u128` returns is identical in both runs; a divergence confirms the finding.
