# Q2592: mWOM.incentiveDeposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `totalConverted` out of agreement with `totalAccumulated` - breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under wombatStaking is holding WOM from an earlier deposit that has not been locked, then assert `totalConverted` and `totalAccumulated` end identical in both runs.
