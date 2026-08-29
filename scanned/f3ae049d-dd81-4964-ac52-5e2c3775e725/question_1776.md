# Q1776: mask-pos via repay: apply a transform after the gate that was supposed to boun

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `amount`, including far above the real debt (the capping path) reach `mask-pos` (mainnet/contracts/market/v0-market-vault.clar:91) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET, the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:91` -> `mask-pos`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `amount`, including far above the real debt (the capping path)
- Exploit idea: `mask-pos` maps an asset id to a bit position, offsetting debt bits by DEBT-OFFSET. Reach it through `repay` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount`, including far above the real debt (the capping path) across its boundary values through `repay` in simnet and assert `mask-pos` never returns a value that breaks the invariant.
