# Q3444: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
In wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount, bool _stake)` while the veWOM mint returns less than the WOM supplied because of the lockDays curve, and drive `rewardRatio` out of agreement with `DENOMINATOR` - breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under the veWOM mint returns less than the WOM supplied because of the lockDays curve, asserting on every row that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
