# Q2396: Airdrop.claim - front-running the first claim to fix the snapshot

## Question
In rewards/Airdrop.sol, because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Does `claim()` let an unprivileged caller exploit that under the token balance held by the contract is below the sum of remaining claimable amounts, so that `sum of all allocations` diverges from `aidropToken.balanceOf(address(this))`, the invariant that the snapshot must not be influenceable by transaction ordering is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: the token balance held by the contract is below the sum of remaining claimable amounts.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the token balance held by the contract is below the sum of remaining claimable amounts, then assert `sum of all allocations` and `aidropToken.balanceOf(address(this))` end identical in both runs.
