# Q2267: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
Note that in wombat/mWomSV.sol, ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2 and force `userUnlockings[user][i].amountInCoolDown` apart from `maxSlot`, breaking the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker arrived through ArbWomUp3.incentiveDeposit with _mode == 2, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `userUnlockings[user][i].amountInCoolDown` versus `maxSlot` relation are unchanged by the attacker's transaction.
