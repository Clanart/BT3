# Q0547: Airdrop.claim - claim reverts once the balance runs short

## Question
In rewards/Airdrop.sol, claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `totalBonus` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that the sum of all claimable amounts must never be allowed to exceed the tokens held - for Critical - Protocol insolvency?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: claim reverts once the balance runs short)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() reverts with InsufficientBalance when claimableAmount exceeds the token balance, and because earlier claimants take an inflated bonus there is no guarantee the remaining balance covers the remaining allocations. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: the sum of all claimable amounts must never be allowed to exceed the tokens held; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation) under most participants have already claimed so totalRemainingAllocation is small, asserting on every row that the sum of all claimable amounts must never be allowed to exceed the tokens held.
