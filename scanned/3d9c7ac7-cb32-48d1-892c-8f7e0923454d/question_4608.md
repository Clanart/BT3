# Q4608: get-available-assets via transfer: make a required price path abort so the position can no lo

## Question
Does `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) let an unprivileged attacker who controls the destination principal, including the market, the market-vault or the treasury reach `get-available-assets` (mainnet/contracts/vault/v0-vault-stx.clar:481) in a state where it make a required price path abort so the position can no longer be closed or seized? Given that it reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:481` -> `get-available-assets`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the destination principal, including the market, the market-vault or the treasury
- Exploit idea: `get-available-assets` reads real liquidity, a different quantity from the `assets` var that `redeem` also gates on. Reach it through `transfer` and make a required price path abort so the position can no longer be closed or seized.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the destination principal, including the market, the market-vault or the treasury across its boundary values through `transfer` in simnet and assert `get-available-assets` never returns a value that breaks the invariant.
