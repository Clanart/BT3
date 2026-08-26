# Q4025: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
rewards/ReferralStorage.sol: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. With the target account, because lockFor is permissionless under attacker control and sharePercent is set so most of the split goes to the referee, can an unprivileged caller sequence `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` so that `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))` no longer reconcile, violating the invariant that a boost weight must not reward splitting one position across addresses and realising High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: sharePercent is set so most of the split goes to the referee.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up sharePercent is set so most of the split goes to the referee, snapshot `userInfos[account].rewardAmount` and `MGP.balanceOf(address(this))`, run the attacker's `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
