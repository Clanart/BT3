# Q4239: is-liquidation-paused via liquidate-multi: attach a price resolved for one asset to a different asset

## Question
`is-liquidation-paused` (mainnet/contracts/market/v0-4-market.clar:691) returns true if the manual pause, the GLOBAL grace entry, OR the per-asset grace entry is live. Can an unprivileged caller of `liquidate-multi` (mainnet/contracts/market/v0-4-market.clar:1593), by choosing which borrowers are placed early versus late in the batch, use that to attach a price resolved for one asset to a different asset in the position, violating the invariant that the LTV a position is judged against belongs to the exact asset set it will hold after the call and producing temporary freezing of funds?

## Target
- File/function: `mainnet/contracts/market/v0-4-market.clar:691` -> `is-liquidation-paused`
- Entrypoint: `liquidate-multi` (`mainnet/contracts/market/v0-4-market.clar:1593`), unprivileged and publicly callable
- Attacker controls: which borrowers are placed early versus late in the batch
- Exploit idea: `is-liquidation-paused` returns true if the manual pause, the GLOBAL grace entry, OR the per-asset grace entry is live. Reach it through `liquidate-multi` and attach a price resolved for one asset to a different asset in the position.
- Invariant to test: the LTV a position is judged against belongs to the exact asset set it will hold after the call
- Expected Immunefi impact: High - temporary freezing of funds
- Fast validation: Snapshot every state variable `is-liquidation-paused` touches, run `liquidate-multi` with which borrowers are placed early versus late in the batch, recompute the invariant off-chain from the snapshot, and assert it matches the on-chain result.
