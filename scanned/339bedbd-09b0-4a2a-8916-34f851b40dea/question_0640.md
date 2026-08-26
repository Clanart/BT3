# Q0640: Airdrop.claim - no reentrancy guard on claim

## Question
In rewards/Airdrop.sol, claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Can an unprivileged attacker reach this through `claim()` while most participants have already claimed so totalRemainingAllocation is small, and drive `periodsEndTime[4]` out of agreement with `block.timestamp` - breaking the invariant that a claim that mutates shared denominators must hold a reentrancy guard - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: most participants have already claimed so totalRemainingAllocation is small.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `periodsEndTime[4]` must stay reconciled with `block.timestamp`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `claim()` sequence atomically under most participants have already claimed so totalRemainingAllocation is small, asserting at the end that `periodsEndTime[4]` still equals `block.timestamp` and the PoC's balance delta is non-positive.
