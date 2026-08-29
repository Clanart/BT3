# Q3968: system-borrow via liquidate-multi: produce a price that passes `oracle-price-legal` while bei

## Question
Does `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593) let an unprivileged attacker who controls the trait principals supplied per entry reach `system-borrow` (mainnet/contracts/vault/v0-vault-stx.clar:865) in a state where it produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude? Given that it independently computes `scaled-amount` with `mul-div-up` from its own `index`, duplicating the market's own scaling of the same borrow, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is permanent freezing of unclaimed yield.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:865` -> `system-borrow`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: the trait principals supplied per entry
- Exploit idea: `system-borrow` independently computes `scaled-amount` with `mul-div-up` from its own `index`, duplicating the market's own scaling of the same borrow. Reach it through `liquidate-multi` and produce a price that passes `oracle-price-legal` while being wrong by orders of magnitude.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - permanent freezing of unclaimed yield
- Fast validation: Write a Clarinet simnet test calling `liquidate-multi` twice with the trait principals supplied per entry varied, and assert that the value `system-borrow` returns is identical in both runs; a divergence confirms the finding.
