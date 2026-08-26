# Q1486: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
In wombat/mWOM.sol, because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Starting from a state where rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting on every row that a shared incentive pot must not be fully claimable by a single actor in one transaction.
