# Q3705: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
In wombat/mWOM.sol, incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Starting from a state where helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged EOA use `deposit(uint256 _amount)` to leave `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is set to a SimplePoolHelper and the attacker uses convertAndStake, call `deposit(uint256 _amount)`, and assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
