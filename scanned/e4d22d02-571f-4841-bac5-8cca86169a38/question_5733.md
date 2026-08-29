# Q5733: get-account-scaled-debt via liquidate: attach a price resolved for one asset to a different asset

## Question
Can an unprivileged attacker entering through `liquidate` (mainnet/contracts/market/v0-4-market.clar:1382), controlling `debt-amount`, drive `get-account-scaled-debt` (mainnet/contracts/market/v0-market-vault.clar:307) — which reads one scaled debt row — to attach a price resolved for one asset to a different asset in the position, breaking the invariant that a position that holds value can always be priced, and therefore always closed, and cause permanent freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-market-vault.clar:307` -> `get-account-scaled-debt`
- Entrypoint: `liquidate` (`mainnet/contracts/market/v0-4-market.clar:1382`), unprivileged and publicly callable
- Attacker controls: `debt-amount`
- Exploit idea: `get-account-scaled-debt` reads one scaled debt row. Reach it through `liquidate` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: a position that holds value can always be priced, and therefore always closed
- Expected Immunefi impact: Critical - permanent freezing of funds
- Fast validation: Snapshot every state variable `get-account-scaled-debt` touches, run `liquidate` with `debt-amount`, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
