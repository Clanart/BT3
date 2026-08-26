# Q0970: mWOM.convertAndStake - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker reach this through `convertAndStake(uint256 _amount)` while rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, then assert `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` end identical in both runs.
