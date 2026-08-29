# Q3552: calc-liq-factor-bound via liquidate-multi: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls which borrowers are placed early versus late in the batch reach `calc-liq-factor-bound` (mainnet/contracts/market/v0-4-market.clar:718) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it scales the penalty between a min and a max, capped at the max, the invariant that a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:718` -> `calc-liq-factor-bound`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `calc-liq-factor-bound` scales the penalty between a min and a max, capped at the max. Reach it through `liquidate-multi` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: a resolved price passed the confidence and staleness gates in the form the gates were designed for, after every transform
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz which borrowers are placed early versus late in the batch across its boundary values through `liquidate-multi` in simnet and assert `calc-liq-factor-bound` never returns a value that breaks the invariant.
