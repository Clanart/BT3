# Q4050: send-tokens via transfer: attach a price resolved for one asset to a different asset

## Question
Entering through `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752) while controlling the destination principal, including the market, the market-vault or the treasury, can an unprivileged attacker make `send-tokens` (mainnet/contracts/market/v0-market-vault.clar:259) attach a price resolved for one asset to a different asset in the position? `send-tokens` pushes an asset to a caller-chosen recipient principal, so the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction would fail, yielding temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:259` -> `send-tokens`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the destination principal, including the market, the market-vault or the treasury
- Exploit idea: `send-tokens` pushes an asset to a caller-chosen recipient principal. Reach it through `transfer` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz the destination principal, including the market, the market-vault or the treasury across its boundary values through `transfer` in simnet and assert `send-tokens` never returns a value that breaks the invariant.
