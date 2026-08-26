# Q1600: Airdrop.claim - no reentrancy guard on claim

## Question
In rewards/Airdrop.sol, claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Can an unprivileged attacker reach this through `claim()` while the attacker's allocation is small relative to the original totalRemainingAllocation, and drive `totalBonus` out of agreement with `aidropToken.balanceOf(address(this))` - breaking the invariant that a claim that mutates shared denominators must hold a reentrancy guard - for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: no reentrancy guard on claim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() performs aidropToken.safeTransfer at the end with no nonReentrant modifier while totalRemainingAllocation and totalBonus have already been mutated, so a token with a transfer hook re-enters against the updated denominators. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a claim that mutates shared denominators must hold a reentrancy guard; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's allocation is small relative to the original totalRemainingAllocation, call `claim()`, and assert `totalBonus` equals `aidropToken.balanceOf(address(this))` and that no account can withdraw more than it put in.
