# Q1740: accrue-user-debts via redeem: produce a price that passes `oracle-price-legal` while bei

## Question
Does `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797) let an unprivileged attacker who controls `amount` of shares burned reach `accrue-user-debts` (mainnet/contracts/market/v0-4-market.clar:259) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it folds accrual over the position's debt list only, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:259` -> `accrue-user-debts`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `amount` of shares burned
- Exploit idea: `accrue-user-debts` folds accrual over the position's debt list only. Reach it through `redeem` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount` of shares burned across its boundary values through `redeem` in simnet and assert `accrue-user-debts` never returns a value that breaks the invariant.
