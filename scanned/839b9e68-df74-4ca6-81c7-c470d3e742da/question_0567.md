# Q0567: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
In wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `IERC20(wom).balanceOf(address(this))` inconsistent with `totalConverted`, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange rewardRatio has been switched on and the contract holds a freshly funded MGP balance, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted` and that no account can withdraw more than it put in.
