# Q5160: calc-utilization via liquidate: judge a position against an LTV belonging to a different a

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls which collateral and debt asset pair is targeted reach `calc-utilization` (mainnet/contracts/vault/v0-vault-stx.clar:164) in a state where it judge a position against an LTV belonging to a different asset set? Given that it divides debt by available liquidity, which can exceed BPS when debt outruns assets, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:164` -> `calc-utilization`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: which collateral and debt asset pair is targeted
- Exploit idea: `calc-utilization` divides debt by available liquidity, which can exceed BPS when debt outruns assets. Reach it through `liquidate` and judge a position against an LTV belonging to a different asset set.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz which collateral and debt asset pair is targeted across its boundary values through `liquidate` in simnet and assert `calc-utilization` never returns a value that breaks the invariant.
