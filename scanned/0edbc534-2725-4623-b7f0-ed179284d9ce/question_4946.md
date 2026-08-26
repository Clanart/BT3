# Q4946: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Under the attacker repeats the call across several addresses in the same block, is there an unprivileged sequence of `convert(uint256 _amount)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that value must not leave the accounting contract before the step that accounts for it has completed, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats the call across several addresses in the same block, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `rewardRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
