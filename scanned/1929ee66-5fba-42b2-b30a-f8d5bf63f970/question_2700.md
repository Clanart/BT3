# Q2700: refresh via liquidate: attach a price resolved for one asset to a different asset

## Question
Does `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382) let an unprivileged attacker who controls `debt-amount` reach `refresh` (mainnet/contracts/market/v0-market-vault.clar:171) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:171` -> `refresh`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `refresh` rewrites `mask` and stamps `last-update` to `stacks-block-time` on every write. Reach it through `liquidate` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `debt-amount` across its boundary values through `liquidate` in simnet and assert `refresh` never returns a value that breaks the invariant.
