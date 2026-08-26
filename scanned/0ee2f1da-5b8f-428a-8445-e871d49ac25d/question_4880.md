# Q4880: mWOM.incentiveDeposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, and drive `totalConverted` out of agreement with `totalAccumulated` - breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
