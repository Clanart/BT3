# Q3121: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
Note that in wombat/mWOM.sol, because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under the attacker calls convertAllWom on WombatStaking in the same transaction and force `totalConverted` apart from `totalAccumulated`, breaking the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `totalConverted` must stay reconciled with `totalAccumulated`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under the attacker calls convertAllWom on WombatStaking in the same transaction, asserting on every row that a shared incentive pot must not be fully claimable by a single actor in one transaction.
