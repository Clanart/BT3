# Q0846: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Starting from a state where rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged EOA use `convert(uint256 _amount)` to leave `IERC20(this).totalSupply()` inconsistent with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(this).totalSupply()` versus `IERC20(wom).balanceOf(wombatStaking) + veWom backing` relation are unchanged by the attacker's transaction.
