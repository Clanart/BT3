# Q4585: mWOM.incentiveDeposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. With _amount with no cap, and _stake, while rewardRatio is non-zero under attacker control and the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, bool _stake)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, asserting on every row that value must not leave the accounting contract before the step that accounts for it has completed.
