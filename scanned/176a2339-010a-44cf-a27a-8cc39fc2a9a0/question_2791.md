# Q2791: mWOM.convertAndStake - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Does `convertAndStake(uint256 _amount)` let an unprivileged caller exploit that under the attacker calls convertAllWom on WombatStaking in the same transaction, so that `IERC20(wom).balanceOf(address(this))` diverges from `totalConverted`, the invariant that value must not leave the accounting contract before the step that accounts for it has completed is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker calls convertAllWom on WombatStaking in the same transaction, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted`, run the attacker's `convertAndStake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
