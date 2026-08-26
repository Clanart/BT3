# Q1903: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
wombat/mWOM.sol: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. With _amount with no cap, and _stake, while rewardRatio is non-zero under attacker control and an owner funding transfer of MGP is sitting in the mempool, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, bool _stake)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up an owner funding transfer of MGP is sitting in the mempool, snapshot `IERC20(wom).balanceOf(address(this))` and `totalConverted`, run the attacker's `incentiveDeposit(uint256 _amount, bool _stake)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
