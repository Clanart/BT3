# Q1444: Airdrop.claim - the bonus numerator grows as others forfeit

## Question
Note that in rewards/Airdrop.sol, claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Can an attacker holding only tokens bought on market reach it via `claim()` under the attacker's allocation is small relative to the original totalRemainingAllocation and force `totalBonus` apart from `aidropToken.balanceOf(address(this))`, breaking the invariant that a bonus share must be fixed against a snapshot taken before any participant could influence it for Critical - Direct theft of user funds?

## Target
- File/function: rewards/Airdrop.sol -> `claim()` (mechanism: the bonus numerator grows as others forfeit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claim()`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the ordering of the claim against every other claimant and against updateEndRemainingAllocation
- Exploit idea: claim() adds every forfeited remainder to totalBonus, and getBonusAmount reads the live totalBonus, so the last claimant sees the largest numerator against the smallest denominator the attacker chose. Precondition: the attacker's allocation is small relative to the original totalRemainingAllocation.
- Invariant to test: a bonus share must be fixed against a snapshot taken before any participant could influence it; concretely, `totalBonus` must stay reconciled with `aidropToken.balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker's allocation is small relative to the original totalRemainingAllocation, snapshot `totalBonus` and `aidropToken.balanceOf(address(this))`, run the attacker's `claim()` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
