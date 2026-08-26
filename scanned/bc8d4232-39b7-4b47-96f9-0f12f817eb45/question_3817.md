# Q3817: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
In wombat/mWOM.sol, incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Starting from a state where helper is set to a SimplePoolHelper and the attacker uses convertAndStake, can an unprivileged EOA use `incentiveDeposit(uint256 _amount, bool _stake)` to leave `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` inconsistent with `IERC20(mgp).balanceOf(address(this))`, violating the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up helper is set to a SimplePoolHelper and the attacker uses convertAndStake, snapshot `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
