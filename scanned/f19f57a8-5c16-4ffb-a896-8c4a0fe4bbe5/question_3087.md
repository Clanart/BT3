# Q3087: mWOM.incentiveDeposit - incentiveDeposit is reachable while the WOM leg is not converted

## Question
Consider wombat/mWOM.sol, where incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Assuming the attacker calls convertAllWom on WombatStaking in the same transaction, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that an incentive paid for committing value must only be paid once the value is actually committed and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit is reachable while the WOM leg is not converted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() passes _doConvert as false, so the WOM it receives stays liquid in this contract while the vlMGP bonus is paid immediately, decoupling the incentive from any commitment. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: an incentive paid for committing value must only be paid once the value is actually committed; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls convertAllWom on WombatStaking in the same transaction, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `rewardRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
