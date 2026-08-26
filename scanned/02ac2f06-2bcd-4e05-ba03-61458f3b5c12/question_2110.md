# Q2110: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Under wombatStaking is holding WOM from an earlier deposit that has not been locked, is there an unprivileged sequence of `convert(uint256 _amount)` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted`, violates the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up wombatStaking is holding WOM from an earlier deposit that has not been locked, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted`, run the attacker's `convert(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
