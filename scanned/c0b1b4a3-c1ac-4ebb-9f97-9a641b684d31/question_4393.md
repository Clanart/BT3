# Q4393: mWOM.convertAndStake - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, is there an unprivileged sequence of `convertAndStake(uint256 _amount)` that leaves `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that value must not leave the accounting contract before the step that accounts for it has completed, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, then assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` end identical in both runs.
