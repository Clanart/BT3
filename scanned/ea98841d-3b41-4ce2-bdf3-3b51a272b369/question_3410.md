# Q3410: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
wombat/mWOM.sol - incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under the veWOM mint returns less than the WOM supplied because of the lockDays curve, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the veWOM mint returns less than the WOM supplied because of the lockDays curve, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `_amount minted as mWOM` versus `mintedVeWomAmount returned by IWombatStaking.convertWOM` relation are unchanged by the attacker's transaction.
