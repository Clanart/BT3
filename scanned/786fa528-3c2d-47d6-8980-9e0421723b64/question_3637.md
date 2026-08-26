# Q3637: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
wombat/mWomSV.sol: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. With _amountToCoolDown and the timestamps written into the slot under attacker control and the attacker repeats cancelUnlock and startUnlock inside one transaction, can an unprivileged caller sequence `startUnlock(uint256 _amountToCoolDown)` so that `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` no longer reconcile, violating the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker repeats cancelUnlock and startUnlock inside one transaction, call `startUnlock(uint256 _amountToCoolDown)`, and assert `getRewardablePercentWAD(user)` equals `_calExpireForfeit in mWOMSVBaseRewarder` and that no account can withdraw more than it put in.
