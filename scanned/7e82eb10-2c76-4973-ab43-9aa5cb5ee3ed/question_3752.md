# Q3752: write-feed via call-ststx-ratio: satisfy the freshness gate with a timestamp the gate was n

## Question
Does `call-ststx-ratio` (mainnet/contracts/market/v0-4-market.clar:1015) let an unprivileged attacker who controls the block and transaction position at which the external ratio is fetched reach `write-feed` (mainnet/contracts/market/v0-4-market.clar:129) in a state where it satisfy the freshness gate with a timestamp the gate was never meant to accept? Given that it applies one Pyth price-feed update and folds its status, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is protocol insolvency.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:129` -> `write-feed`
- Entrypoint: `call-ststx-ratio` (`mainnet/contracts/market/v0-4-market.clar:1015`), unprivileged and publicly callable
- Attacker controls: the block and transaction position at which the external ratio is fetched
- Exploit idea: `write-feed` applies one Pyth price-feed update and folds its status. Reach it through `call-ststx-ratio` and satisfy the freshness gate with a timestamp the gate was never meant to accept.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Write a Clarinet simnet test calling `call-ststx-ratio` twice with the block and transaction position at which the external ratio is fetched varied, and assert that the value `write-feed` returns is identical in both runs; a divergence confirms the finding.
