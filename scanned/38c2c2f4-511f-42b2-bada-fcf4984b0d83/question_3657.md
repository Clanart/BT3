# Q3657: mWOM.convertAndStake - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, is there an unprivileged sequence of `convertAndStake(uint256 _amount)` that leaves `IERC20(this).totalSupply()` unreconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violates the invariant that value must not leave the accounting contract before the step that accounts for it has completed, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up helper is set to a SimplePoolHelper and the attacker uses convertAndStake, snapshot `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, run the attacker's `convertAndStake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
