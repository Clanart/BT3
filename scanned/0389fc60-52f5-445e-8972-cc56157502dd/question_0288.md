# Q0288: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
Consider wombat/mWOM.sol, where incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Assuming rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged attacker turn this into a divergence between `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` via `deposit(uint256 _amount)`, breaking the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio has been switched on and the contract holds a freshly funded MGP balance, call `deposit(uint256 _amount)`, and assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
