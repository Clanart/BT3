# Q2018: mWOM.incentiveDeposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
Consider wombat/mWOM.sol, where for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Assuming an owner funding transfer of MGP is sitting in the mempool, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount, bool _stake)`: constrain the setup so that an owner funding transfer of MGP is sitting in the mempool, fuzz the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero), and assert after every call that value must not leave the accounting contract before the step that accounts for it has completed.
