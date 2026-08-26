# Q4968: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Consider wombat/mWOM.sol, where the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Assuming the attacker repeats the call across several addresses in the same block, can an unprivileged attacker turn this into a divergence between `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` via `convertAndStake(uint256 _amount)`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAndStake(uint256 _amount)`: constrain the setup so that the attacker repeats the call across several addresses in the same block, fuzz the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM), and assert after every call that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding.
