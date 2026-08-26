# Q2668: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
wombat/mWomSV.sol: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, is there an unprivileged sequence of `startUnlock(uint256 _amountToCoolDown)` that leaves `mWomSV.getUserTotalLocked(user)` unreconciled with `ArbWomUp3.calDoubledCounted(user)`, violates the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, call `startUnlock(uint256 _amountToCoolDown)`, and assert `mWomSV.getUserTotalLocked(user)` equals `ArbWomUp3.calDoubledCounted(user)` and that no account can withdraw more than it put in.
