# Q3861: mWomSV.startUnlock - getUserTotalLocked underflow bricks the position

## Question
wombat/mWomSV.sol: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. With _amountToCoolDown and the timestamps written into the slot under attacker control and the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that the locked-balance accessor must never revert, and a user must always be able to read and exit and realising Critical - Permanent freezing of funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: getUserTotalLocked underflow bricks the position)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: getUserTotalLocked() subtracts getUserAmountInCoolDown(user) from the MasterMagpie stake with no floor, so any divergence between the two makes balanceOf, getRewardablePercentWAD, startUnlock and every rewarder settlement revert permanently for that account. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: the locked-balance accessor must never revert, and a user must always be able to read and exit; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, call `startUnlock(uint256 _amountToCoolDown)`, and assert `totalAmount` equals `IERC20(mWOM).balanceOf(address(this))` and that no account can withdraw more than it put in.
