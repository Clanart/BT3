# Q5628: create via transfer: attach a price resolved for one asset to a different asset

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls `amount` reach `create` (mainnet/contracts/market/v0-market-vault.clar:150) in a state where it attach a price resolved for one asset to a different asset in the position? Given that it binds a principal to a fresh numeric id, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:150` -> `create`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: `amount`
- Exploit idea: `create` binds a principal to a fresh numeric id. Reach it through `transfer` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` across its boundary values through `transfer` in simnet and assert `create` never returns a value that breaks the invariant.
