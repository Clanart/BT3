# Q0071: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol - for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker controlling _amount, and the block relative to any pending convertAllWom, under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, exploit this through `convert(uint256 _amount)` to break the reconciliation between `totalConverted` and `totalAccumulated` and the invariant that value must not leave the accounting contract before the step that accounts for it has completed, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, and the block relative to any pending convertAllWom) under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, asserting on every row that value must not leave the accounting contract before the step that accounts for it has completed.
