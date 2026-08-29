# Q0468: zip via collateral-remove: attach a price resolved for one asset to a different asset

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls the set of assets held reach `zip` (mainnet/contracts/vault/v0-vault-stx.clar:226) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it pairs the utilization and rate point lists element by element, the invariant that collateral is valued low and debt is valued high at every call site without exception breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:226` -> `zip`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: the set of assets held
- Exploit idea: `zip` pairs the utilization and rate point lists element by element. Reach it through `collateral-remove` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: collateral is valued low and debt is valued high at every call site without exception
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the set of assets held across its boundary values through `collateral-remove` in simnet and assert `zip` never returns a value that breaks the invariant.
