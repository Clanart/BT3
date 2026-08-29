# Q2112: collateral-remove via liquidate-redeem: attach a price resolved for one asset to a different asset

## Question
Does `liquidate-redeem` (mainnet/contracts/market/v0-4-market.clar:1604) let an unprivileged attacker who controls the redemption receiver reach `collateral-remove` (mainnet/contracts/market/v0-market-vault.clar:406) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it decrements the map and writes the entry before `send-tokens` executes, the invariant that a position that holds value can always be priced, and therefore always closed breaks and the result is permanent freezing of a position that can never be closed.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:406` -> `collateral-remove`
- Entrypoint: `liquidate-redeem` (`mainnet/contracts/market/v0-4-market.clar:1604`), unprivileged and publicly callable
- Attacker controls: the redemption receiver
- Exploit idea: `collateral-remove` decrements the map and writes the entry before `send-tokens` executes. Reach it through `liquidate-redeem` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of a position that can never be closed
- Fast validation: Fuzz the redemption receiver across its boundary values through `liquidate-redeem` in simnet and assert `collateral-remove` never returns a value that breaks the invariant.
