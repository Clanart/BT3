# Q3621: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
In wombat/mWomSV.sol, ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Can an unprivileged attacker reach this through `startUnlock(uint256 _amountToCoolDown)` while the attacker repeats cancelUnlock and startUnlock inside one transaction, and drive `totalAmount` out of agreement with `IERC20(mWOM).balanceOf(address(this))` - breaking the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats cancelUnlock and startUnlock inside one transaction, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `totalAmount` versus `IERC20(mWOM).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
