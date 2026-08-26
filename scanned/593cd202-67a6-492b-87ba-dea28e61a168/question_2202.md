# Q2202: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol - the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an unprivileged attacker controlling _amount and the helper routing that stakes the freshly minted mWOM, under wombatStaking is holding WOM from an earlier deposit that has not been locked, exploit this through `convertAndStake(uint256 _amount)` to break the reconciliation between `totalConverted` and `totalAccumulated` and the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange wombatStaking is holding WOM from an earlier deposit that has not been locked, call `convertAndStake(uint256 _amount)`, and assert `totalConverted` equals `totalAccumulated` and that no account can withdraw more than it put in.
