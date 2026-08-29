# Q4169: calc-index-next via redeem: attach a price resolved for one asset to a different asset

## Question
Can an unprivileged attacker entering through `redeem` (mainnet/contracts/vault/v0-vault-stx.clar:797), controlling `recipient`, drive `calc-index-next` (mainnet/contracts/vault/v0-vault-stx.clar:183) — which applies a multiplier to the current index — to attach a price resolved for one asset to a different asset in the position, breaking the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call, and cause protocol insolvency?

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:183` -> `calc-index-next`
- Entrypoint: `redeem` (`mainnet/contracts/vault/v0-vault-stx.clar:797`), unprivileged and publicly callable
- Attacker controls: `recipient`
- Exploit idea: `calc-index-next` applies a multiplier to the current index. Reach it through `redeem` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - protocol insolvency
- Fast validation: Run the baseline `redeem` call, then the attacker-shaped one with `recipient`, and assert the attacker's net token balance change is zero or negative.
