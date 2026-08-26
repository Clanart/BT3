# Q1224: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Does `claim()` let an unprivileged caller exploit that under totalBonus has grown large from earlier forfeits, so that `getClaimableAmount(user)` diverges from `allocations[user]`, the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under totalBonus has grown large from earlier forfeits, asserting on every row that the sum of all claimable amounts must never be allowed to exceed the tokens held.
