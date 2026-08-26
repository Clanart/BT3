# Q0873: mWomSV.startUnlock - ArbWomUp3 tier reads the same locked balance the deposit just changed

## Question
wombat/mWomSV.sol - ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under the attacker's slot matured one block ago, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` and the invariant that a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: ArbWomUp3 tier reads the same locked balance the deposit just changed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: ArbWomUp3.getRewardAmount and calDoubledCounted both read mWomSV.getUserTotalLocked(_account), and ArbWomUp3._deposit mode 2 locks into mWomSV before the reward is computed, so the tier input and the double-count subtraction are taken from the post-deposit balance. Precondition: the attacker's slot matured one block ago.
- Invariant to test: a tier bonus and the double-count correction that offsets it must be computed against the same balance snapshot; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker's slot matured one block ago, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getUserAmountInCoolDown(user)` equals `totalAmountInCoolDown` and that no account can withdraw more than it put in.
