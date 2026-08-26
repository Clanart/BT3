# Q1407: mWOM.incentiveDeposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, bool _stake)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that value must not leave the accounting contract before the step that accounts for it has completed, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting on every row that value must not leave the accounting contract before the step that accounts for it has completed.
