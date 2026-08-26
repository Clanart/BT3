# Q0195: mWOM.convertAndStake - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. With _amount and the helper routing that stakes the freshly minted mWOM under attacker control and rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged caller sequence `convertAndStake(uint256 _amount)` so that `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` no longer reconcile, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `convertAndStake(uint256 _amount)` sequence atomically under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, asserting at the end that `IERC20(this).totalSupply()` still equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the PoC's balance delta is non-positive.
