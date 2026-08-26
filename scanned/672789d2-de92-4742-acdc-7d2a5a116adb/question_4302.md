# Q4302: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
In wombat/mWOM.sol, because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Starting from a state where helper is unset so convertAndStake reverts and only the plain mint path is reachable, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up helper is unset so convertAndStake reverts and only the plain mint path is reachable, snapshot `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
