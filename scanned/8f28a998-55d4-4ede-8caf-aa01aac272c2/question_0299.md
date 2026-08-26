# Q0299: Airdrop.claim - no reentrancy guard on claim

## Question
In rewards/Airdrop.sol, claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Starting from a state where block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, can an unprivileged EOA use `claim()` to leave `getClaimableAmount(user)` inconsistent with `allocations[user]`, violating the invariant that a claim that mutates shared denominators must hold a reentrancy guard and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: block.timestamp has just passed periodsEndTime[4] and no one has claimed yet.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish block.timestamp has just passed periodsEndTime[4] and no one has claimed yet, have the attacker run `claim()`, then assert the victim's claimable value and the `getClaimableAmount(user)` versus `allocations[user]` relation are unchanged by the attacker's transaction.
