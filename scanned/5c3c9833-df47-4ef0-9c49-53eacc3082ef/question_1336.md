# Q1336: Airdrop.claim - front-running the first claim to fix the snapshot

## Question
rewards/Airdrop.sol: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. With the ordering of the claim against every other claimant and against updateEndRemainingAllocation under attacker control and totalBonus has grown large from earlier forfeits, can an unprivileged caller sequence `claim()` so that `totalBonus` and `aidropToken.balanceOf(address(this))` no longer reconcile, violating the invariant that the snapshot must not be influenceable by transaction ordering and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: front-running the first claim to fix the snapshot)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: because the snapshot is taken inside the first claim only when the stored value is still zero, an attacker can call updateEndRemainingAllocation ahead of that first claim and fix the denominator at a value of their choosing. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: the snapshot must not be influenceable by transaction ordering; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalBonus has grown large from earlier forfeits, then assert `totalBonus` and `aidropToken.balanceOf(address(this))` end identical in both runs.
