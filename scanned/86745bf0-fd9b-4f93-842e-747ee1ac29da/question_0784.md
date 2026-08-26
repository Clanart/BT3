# Q0784: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. With _amount, and the block relative to any pending convertAllWom under attacker control and rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged caller sequence `convert(uint256 _amount)` so that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convert(uint256 _amount)`: constrain the setup so that rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, fuzz the attacker inputs (_amount, and the block relative to any pending convertAllWom), and assert after every call that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
