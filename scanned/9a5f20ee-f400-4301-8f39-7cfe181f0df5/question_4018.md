# Q4018: mWOM.convertAndStake - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol - the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an unprivileged attacker controlling _amount and the helper routing that stakes the freshly minted mWOM, under helper is unset so convertAndStake reverts and only the plain mint path is reachable, exploit this through `convertAndStake(uint256 _amount)` to break the reconciliation between `rewardRatio` and `DENOMINATOR` and the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange helper is unset so convertAndStake reverts and only the plain mint path is reachable, call `convertAndStake(uint256 _amount)`, and assert `rewardRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.
