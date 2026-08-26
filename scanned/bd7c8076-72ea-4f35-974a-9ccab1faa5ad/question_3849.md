# Q3849: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
In wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Does `incentiveDeposit(uint256 _amount, bool _stake)` let an unprivileged caller exploit that under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, so that `IERC20(wom).balanceOf(address(this))` diverges from `totalConverted`, the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting on every row that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
