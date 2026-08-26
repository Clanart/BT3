# Q1482: mWomSV.unlock - matured slot decays the rewardable percent toward zero

## Question
In wombat/mWomSV.sol, for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Starting from a state where the attacker reached maxSlot so slot reuse is forced, can an unprivileged EOA use `unlock(uint256 _slotIndex)` to leave `getUserTotalLocked(user)` inconsistent with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, violating the invariant that a user must not lose vested value merely because they redeemed late and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `unlock(uint256 _slotIndex)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unlock(uint256 _slotIndex)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _slotIndex and the redemption timing
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_slotIndex and the redemption timing) under the attacker reached maxSlot so slot reuse is forced, asserting on every row that a user must not lose vested value merely because they redeemed late.
