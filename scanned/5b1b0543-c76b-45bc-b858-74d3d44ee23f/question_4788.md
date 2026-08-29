# Q4788: total-debt via collateral-remove-redeem: apply a transform after the gate that was supposed to boun

## Question
Does `collateral-remove-redeem` (mainnet/contracts/market/v0-4-market.clar:1211) let an unprivileged attacker who controls `min-underlying` reach `total-debt` (mainnet/contracts/vault/v0-vault-stx.clar:328) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it computes cumulative debt from `principal-scaled` and `index`, the invariant that each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction breaks and the result is temporary freezing of funds.

## Target
- File/function: `mainnet/contracts/vault/v0-vault-stx.clar:328` -> `total-debt`
- Entrypoint: `collateral-remove-redeem` (`mainnet/contracts/market/v0-4-market.clar:1211`), unprivileged and publicly callable
- Attacker controls: `min-underlying`
- Exploit idea: `total-debt` computes cumulative debt from `principal-scaled` and `index`. Reach it through `collateral-remove-redeem` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: each callcode preserves magnitude and sign and cannot be moved by the caller in the same transaction
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Fuzz `min-underlying` across its boundary values through `collateral-remove-redeem` in simnet and assert `total-debt` never returns a value that breaks the invariant.
