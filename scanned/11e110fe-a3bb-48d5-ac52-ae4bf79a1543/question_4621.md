# Q4621: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
Consider wombat/mWOM.sol, where because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Assuming the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged attacker turn this into a divergence between `rewardRatio` and `DENOMINATOR` via `incentiveDeposit(uint256 _amount, bool _stake)`, breaking the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `rewardRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
