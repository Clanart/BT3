# Q4197: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
wombat/mWOM.sol: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Under helper is unset so convertAndStake reverts and only the plain mint path is reachable, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, bool _stake)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under helper is unset so convertAndStake reverts and only the plain mint path is reachable, then assert `rewardRatio` and `DENOMINATOR` end identical in both runs.
