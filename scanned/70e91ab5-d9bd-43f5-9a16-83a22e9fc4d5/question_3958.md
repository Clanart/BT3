# Q3958: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Under helper is unset so convertAndStake reverts and only the plain mint path is reachable, is there an unprivileged sequence of `convert(uint256 _amount)` that leaves `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` unreconciled with `IERC20(mgp).balanceOf(address(this))`, violates the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `convert(uint256 _amount)` sequence atomically under helper is unset so convertAndStake reverts and only the plain mint path is reachable, asserting at the end that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
