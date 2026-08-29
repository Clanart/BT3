# Q4248: send-tokens via collateral-remove: produce a price that passes `oracle-price-legal` while bei

## Question
Does `collateral-remove` (mainnet/contracts/market/v0-4-market.clar:1107) let an unprivileged attacker who controls whether the position has any enabled debt row (the has-debt branch) reach `send-tokens` (mainnet/contracts/market/v0-market-vault.clar:259) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it pushes an asset to a caller-chosen recipient principal, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:259` -> `send-tokens`
- Entrypoint: `collateral-remove` (`mainnet/contracts/market/v0-4-market.clar:1107`), unprivileged and publicly callable
- Attacker controls: whether the position has any enabled debt row (the has-debt branch)
- Exploit idea: `send-tokens` pushes an asset to a caller-chosen recipient principal. Reach it through `collateral-remove` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz whether the position has any enabled debt row (the has-debt branch) across its boundary values through `collateral-remove` in simnet and assert `send-tokens` never returns a value that breaks the invariant.
