# Q0720: calc-liq-debt-repay-real via liquidate-multi: attach a price resolved for one asset to a different asset

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `calc-liq-debt-repay-real` (mainnet/contracts/market/v0-4-market.clar:733) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:733` -> `calc-liq-debt-repay-real`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `calc-liq-debt-repay-real` re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`. Reach it through `liquidate-multi` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the trait principals supplied per entry across its boundary values through `liquidate-multi` in simnet and assert `calc-liq-debt-repay-real` never returns a value that breaks the invariant.
