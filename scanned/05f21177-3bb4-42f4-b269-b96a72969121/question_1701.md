# Q1701: Airdrop.claim - re-snapshotting after other claims collapses the denominator

## Question
Note that in rewards/Airdrop.sol, each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Can an attacker holding only tokens bought on market reach it via `claim()` under the attacker's allocation is the largest remaining one and force `totalBonus` apart from `aidropToken.balanceOf(address(this))`, breaking the invariant that a participant must not be able to shrink the denominator that scales their own bonus for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: re-snapshotting after other claims collapses the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: each claim() reduces totalRemainingAllocation, so an attacker who waits until most allocations are claimed and then calls updateEndRemainingAllocation drives the bonus denominator toward their own remaining allocation. Precondition: the attacker's allocation is the largest remaining one.
- Invariant to test: a participant must not be able to shrink the denominator that scales their own bonus; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's allocation is the largest remaining one, have the attacker run `claim()`, then assert the victim's claimable value and the `totalBonus` versus `aidropToken.balanceOf(address(this))` relation are unchanged by the attacker's transaction.
