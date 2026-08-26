# Q3769: mWOM.deposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while helper is set to a SimplePoolHelper and the attacker uses convertAndStake, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up helper is set to a SimplePoolHelper and the attacker uses convertAndStake, snapshot `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
