# Q1611: VLMGP.unlock - matured slot left unredeemed decays the rewardable percent for everyone

## Question
Consider VLMGP.sol, where for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Assuming coolDownInSecs is at its configured production value and endTime is far in the future, can an unprivileged attacker turn this into a divergence between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` via `unlock(uint256 _slotIndex)`, breaking the invariant that value must not be confiscated purely because a user delayed a redemption they were entitled to make and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot left unredeemed decays the rewardable percent for everyone)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and how long after endTime the slot is redeemed
- Exploit idea: for a slot whose endTime has passed, getRewardablePercentWAD adds amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), a term that shrinks without bound, so a user who never calls unlock has their whole vesting entitlement forfeited to the pool. Precondition: coolDownInSecs is at its configured production value and endTime is far in the future.
- Invariant to test: value must not be confiscated purely because a user delayed a redemption they were entitled to make; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange coolDownInSecs is at its configured production value and endTime is far in the future, call `unlock(uint256 _slotIndex)`, and assert `getUserTotalLocked(user)` equals `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` and that no account can withdraw more than it put in.
