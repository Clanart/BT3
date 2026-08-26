# Q0133: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, is there an unprivileged sequence of `convertAndStake(uint256 _amount)` that leaves `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio has been switched on and the contract holds a freshly funded MGP balance, call `convertAndStake(uint256 _amount)`, and assert `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` equals `IERC20(mgp).balanceOf(address(this))` and that no account can withdraw more than it put in.
