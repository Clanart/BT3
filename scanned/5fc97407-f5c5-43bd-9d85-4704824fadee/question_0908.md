# Q0908: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Note that in wombat/mWOM.sol, the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit and force `rewardRatio` apart from `DENOMINATOR`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, snapshot `rewardRatio` and `DENOMINATOR`, run the attacker's `convertAndStake(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
