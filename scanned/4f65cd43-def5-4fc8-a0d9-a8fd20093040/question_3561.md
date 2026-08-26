# Q3561: mWOM.convert - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
Consider wombat/mWOM.sol, where the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Assuming helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged attacker turn this into a divergence between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` via `convert(uint256 _amount)`, breaking the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish helper is set to a SimplePoolHelper and the attacker uses convertAndStake, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `_amount minted as mWOM` versus `mintedVeWomAmount returned by IWombatStaking.convertWOM` relation are unchanged by the attacker's transaction.
