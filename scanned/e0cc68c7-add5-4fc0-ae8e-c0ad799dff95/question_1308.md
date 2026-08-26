# Q1308: Airdrop.claim - no reentrancy guard on claim

## Question
rewards/Airdrop.sol - claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Can an unprivileged attacker controlling the ordering of the claim against every other claimant and against updateEndRemainingAllocation, under totalBonus has grown large from earlier forfeits, exploit this through `claim()` to break the reconciliation between `totalEndRemainingAllocation` and `totalRemainingAllocation` and the invariant that a claim that mutates shared denominators must hold a reentrancy guard, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: totalBonus has grown large from earlier forfeits.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `totalEndRemainingAllocation` must stay reconciled with `totalRemainingAllocation`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `claim()`: constrain the setup so that totalBonus has grown large from earlier forfeits, fuzz the attacker inputs (the ordering of the claim against every other claimant and against updateEndRemainingAllocation), and assert after every call that a claim that mutates shared denominators must hold a reentrancy guard.
