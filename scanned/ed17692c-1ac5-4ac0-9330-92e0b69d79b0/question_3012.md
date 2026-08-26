# Q3012: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
wombat/mWomSV.sol: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Under the attacker holds a second address so lockFor can be used across two accounts, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `getUserTotalLocked(user)` unreconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`, violates the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker holds a second address so lockFor can be used across two accounts, then assert `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` end identical in both runs.
