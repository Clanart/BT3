# Q1326: check-confidence via liquidate-redeem: judge a position against an LTV belonging to a different a

## Question
Entering through `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) while controlling the vault whose share price the redemption moves, can an unprivileged attacker make `check-confidence` (mainnet/contracts/market/v0-4-market.clar:305) judge a position against an LTV belonging to a different asset set? `check-confidence` compares the Pyth confidence interval against `max-confidence-ratio` in BPS, a gate that has no DIA equivalent, so the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call would fail, yielding permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:305` -> `check-confidence`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the vault whose share price the redemption moves
- Exploit idea: `check-confidence` compares the Pyth confidence interval against `max-confidence-ratio` in BPS, a gate that has no DIA equivalent. Reach it through `liquidate-redeem` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz the vault whose share price the redemption moves across its boundary values through `liquidate-redeem` in simnet and assert `check-confidence` never returns a value that breaks the invariant.
