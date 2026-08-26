# Q1430: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
Consider wombat/mWomSV.sol, where VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Assuming the attacker reached maxSlot so slot reuse is forced, can an unprivileged attacker turn this into a divergence between `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder` via `startUnlock(uint256 _amountToCoolDown)`, breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `getRewardablePercentWAD(user)` must stay reconciled with `_calExpireForfeit in mWOMSVBaseRewarder`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker reached maxSlot so slot reuse is forced, snapshot `getRewardablePercentWAD(user)` and `_calExpireForfeit in mWOMSVBaseRewarder`, run the attacker's `startUnlock(uint256 _amountToCoolDown)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
