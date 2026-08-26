# Q3669: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
Note that in wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an attacker holding only tokens bought on market reach it via `unlock(uint256 _slotIndex)` under the attacker repeats cancelUnlock and startUnlock inside one transaction and force `getUserTotalLocked(user)` apart from `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, breaking the invariant that a user must not lose vested value merely because they redeemed late for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats cancelUnlock and startUnlock inside one transaction, call `unlock(uint256 _slotIndex)`, and assert `getUserTotalLocked(user)` equals `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and that no account can withdraw more than it put in.
