# Q3138: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
In wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Starting from a state where the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged EOA use `convert(uint256 _amount)` to leave `IERC20(this).totalSupply()` inconsistent with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the veWOM mint returns less than the WOM supplied because of the lockDays curve, call `convert(uint256 _amount)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
