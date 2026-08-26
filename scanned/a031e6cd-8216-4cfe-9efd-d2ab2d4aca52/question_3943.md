# Q3943: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
wombat/mWOM.sol - because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting at the end that `_amount minted as mWOM` still equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the PoC's balance delta is non-positive.
