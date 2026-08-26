# Q2120: Airdrop.claim - no reentrancy guard on claim

## Question
In rewards/Airdrop.sol, claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Can an unprivileged attacker reach this through `claim()` while the first honest claim transaction is pending in the mempool, and drive `getClaimableAmount(user)` out of agreement with `allocations[user]` - breaking the invariant that a claim that mutates shared denominators must hold a reentrancy guard - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: the first honest claim transaction is pending in the mempool.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `getClaimableAmount(user)` must stay reconciled with `allocations[user]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the first honest claim transaction is pending in the mempool, have the attacker run `claim()`, then assert the victim's claimable value and the `getClaimableAmount(user)` versus `allocations[user]` relation are unchanged by the attacker's transaction.
