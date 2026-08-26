# Q1616: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Consider wombat/mWOM.sol, where the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Assuming an owner funding transfer of MGP is sitting in the mempool, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted` via `convertAndStake(uint256 _amount)`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAndStake(uint256 _amount)`: constrain the setup so that an owner funding transfer of MGP is sitting in the mempool, fuzz the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM), and assert after every call that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
