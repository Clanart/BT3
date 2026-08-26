# Q4657: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
Note that in wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an attacker holding only tokens bought on market reach it via `convert(uint256 _amount)` under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance and force `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convert(uint256 _amount)`: constrain the setup so that the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, fuzz the attacker inputs (_amount, and the block relative to any pending convertAllWom), and assert after every call that value must not leave the accounting contract before the step that accounts for it has completed.
