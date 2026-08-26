# Q2087: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
wombat/mWOM.sol - because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under an owner funding transfer of MGP is sitting in the mempool, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `rewardRatio` and `DENOMINATOR` and the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no cap, and _stake, while rewardRatio is non-zero) under an owner funding transfer of MGP is sitting in the mempool, asserting on every row that a shared incentive pot must not be fully claimable by a single actor in one transaction.
