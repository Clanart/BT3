# Q4341: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
Consider wombat/mWOM.sol, where for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Assuming the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged attacker turn this into a divergence between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` via `convert(uint256 _amount)`, breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `convert(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
