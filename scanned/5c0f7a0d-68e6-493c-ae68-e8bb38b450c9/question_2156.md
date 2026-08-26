# Q2156: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. With _amount, and the block relative to any pending convertAllWom under attacker control and wombatStaking is holding WOM from an earlier deposit that has not been locked, can an unprivileged caller sequence `convert(uint256 _amount)` so that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange wombatStaking is holding WOM from an earlier deposit that has not been locked, call `convert(uint256 _amount)`, and assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
