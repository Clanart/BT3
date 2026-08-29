# Q0968: merge-price via collateral-add: apply a transform after the gate that was supposed to boun

## Question
Does `collateral-add` (mainnet/contracts/market/v0-4-market.clar:1020) let an unprivileged attacker who controls `amount` reach `merge-price` (mainnet/contracts/market/v0-4-market.clar:506) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it attaches a price to an asset record by position in the fold, not by asset id, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is protocol insolvency through uncollateralised debt.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:506` -> `merge-price`
- Entrypoint: `collateral-add` (`mainnet/contracts/market/v0-4-market.clar:1020`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `merge-price` attaches a price to an asset record by position in the fold, not by asset id. Reach it through `collateral-add` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - protocol insolvency through uncollateralised debt
- Fast validation: Write a Clarinet simnet test calling `collateral-add` twice with `amount` varied, and assert that the value `merge-price` returns is identical in both runs; a divergence confirms the finding.
