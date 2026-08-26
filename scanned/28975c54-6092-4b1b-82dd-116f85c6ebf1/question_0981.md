# Q0981: Airdrop.claim - no reentrancy guard on claim

## Question
In rewards/Airdrop.sol, claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Does `claim()` let an unprivileged caller exploit that under exactly one unclaimed allocation remains besides the attacker's, so that `sum of all allocations` diverges from `aidropToken.balanceOf(address(this))`, the invariant that a claim that mutates shared denominators must hold a reentrancy guard is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: exactly one unclaimed allocation remains besides the attacker's.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `sum of all allocations` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under exactly one unclaimed allocation remains besides the attacker's, then assert `sum of all allocations` and `aidropToken.balanceOf(address(this))` end identical in both runs.
