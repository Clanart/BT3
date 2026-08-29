# Q5232: debt-remove-scaled via repay: apply a transform after the gate that was supposed to boun

## Question
Does `repay` (mainnet/contracts/market/v0-4-market.clar:1316) let an unprivileged attacker who controls `amount`, including far above the real debt (the capping path) reach `debt-remove-scaled` (mainnet/contracts/market/v0-market-vault.clar:473) in a state where it apply a transform after the gate that was supposed to bound its output? Given that it clears the debt bit only when the remaining scaled debt is exactly zero, the invariant that every price is attached to the asset it was resolved for, and each asset enters the totals exactly once breaks and the result is permanent freezing of funds.

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:473` -> `debt-remove-scaled`
- Entrypoint: `repay` (`mainnet/contracts/market/v0-4-market.clar:1316`), unprivileged and publicly callable
- Attacker controls: `amount`, including far above the real debt (the capping path)
- Exploit idea: `debt-remove-scaled` clears the debt bit only when the remaining scaled debt is exactly zero. Reach it through `repay` and apply a transform after the gate that was supposed to bound its output.
- Invariant to test: every price is attached to the asset it was resolved for, and each asset enters the totals exactly once
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Fuzz `amount`, including far above the real debt (the capping path) across its boundary values through `repay` in simnet and assert `debt-remove-scaled` never returns a value that breaks the invariant.
