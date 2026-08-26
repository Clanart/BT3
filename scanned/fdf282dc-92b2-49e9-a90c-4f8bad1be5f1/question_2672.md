# Q2672: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. With _amount, and the block relative to any pending convertAllWom under attacker control and the attacker calls convertAllWom on WombatStaking in the same transaction, can an unprivileged caller sequence `convert(uint256 _amount)` so that `totalConverted` and `totalAccumulated` no longer reconcile, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker calls convertAllWom on WombatStaking in the same transaction, then assert `totalConverted` and `totalAccumulated` end identical in both runs.
