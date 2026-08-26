# Q1326: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Consider wombat/mWOM.sol, where the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Assuming rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged attacker turn this into a divergence between `totalConverted` and `totalAccumulated` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `totalConverted` versus `totalAccumulated` relation are unchanged by the attacker's transaction.
