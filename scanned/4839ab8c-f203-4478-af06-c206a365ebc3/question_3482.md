# Q3482: write-feed via liquidate: produce a price that passes `oracle-price-legal` while bei

## Question
Entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) while controlling which collateral and debt asset pair is targeted, can an unprivileged attacker make `write-feed` (mainnet/contracts/market/v0-4-market.clar:129) produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? `write-feed` applies one Pyth price-feed update and folds its status, so the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform would fail, yielding protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `liquidate` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `liquidate` twice with which collateral and debt asset pair is targeted varied, and assert that the value `write-feed` returns is identical in both runs; a divergence confirms the finding.
