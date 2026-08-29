# Q5285: receive-tokens via transfer: attach a price resolved for one asset to a different asset

## Question
Can an unprivileged attacker entering through `transfer` (mainnet/contracts/vault/v0-vault-stx.clar:752), controlling the destination principal, including the market, the market-vault or the treasury, drive `receive-tokens` (mainnet/contracts/market/v0-market-vault.clar:256) — which pulls an asset from a named account — to attach a price resolved for one asset to a different asset in the position, breaking the invariant that a position that holds value can always be priced, and therefore always closed, and cause protocol insolvency?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:256` -> `receive-tokens`
- Entrypoint: `transfer` (`mainnet/contracts/vault/v0-vault-stx.clar:752`), unprivileged and publicly callable
- Attacker controls: the destination principal, including the market, the market-vault or the treasury
- Exploit idea: `receive-tokens` pulls an asset from a named account. Reach it through `transfer` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Run the baseline `transfer` call, then the attacker-shaped one with the destination principal, including the market, the market-vault or the treasury, and assert the attacker's net token balance change is zero or negative.
