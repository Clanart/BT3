# Q5892: calc-liq-debt-repay-real via liquidate-redeem: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `calc-liq-debt-repay-real` (mainnet/contracts/market/v0-4-market.clar:733) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:733` -> `calc-liq-debt-repay-real`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `calc-liq-debt-repay-real` re-derives debt from capped collateral by dividing by `(+ BPS liq-penalty)`. Reach it through `liquidate-redeem` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the redemption receiver across its boundary values through `liquidate-redeem` in simnet and assert `calc-liq-debt-repay-real` never returns a value that breaks the invariant.
