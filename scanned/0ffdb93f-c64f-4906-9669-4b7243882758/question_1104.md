# Q1104: ReferralStorage.updateTotalFactor - sqrt factor makes many small accounts dominate the denominator

## Question
In rewards/ReferralStorage.sol, userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Can an unprivileged attacker reach this through `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` while the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, and drive `userInfos[account].rewardAmount` out of agreement with `MGP.balanceOf(address(this))` - breaking the invariant that a boost weight must not reward splitting one position across addresses - for High - Theft of unclaimed yield?

## Target
- File/function: rewards/ReferralStorage.sol -> `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` (mechanism: sqrt factor makes many small accounts dominate the denominator)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the target account, because lockFor is permissionless
- Exploit idea: userInfo.factor is DSMath.sqrt(lockedAmount), so splitting one lock across many addresses raises the summed factor relative to a single large lock and shifts the shared BoostPoint toward the splitter. Precondition: the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor.
- Invariant to test: a boost weight must not reward splitting one position across addresses; concretely, `userInfos[account].rewardAmount` must stay reconciled with `MGP.balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `updateTotalFactor(address _account) via VLMGP.lockFor and startUnlock` sequence atomically under the attacker is the only account that has ever registered a code so totalBoostFactor equals their own factor, asserting at the end that `userInfos[account].rewardAmount` still equals `MGP.balanceOf(address(this))` and the PoC's balance delta is non-positive.
