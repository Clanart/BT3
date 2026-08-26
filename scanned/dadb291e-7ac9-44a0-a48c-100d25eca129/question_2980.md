# Q2980: mWOM.incentiveDeposit - incentiveDeposit has no cap on the MGP it pays out

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under the attacker calls convertAllWom on WombatStaking in the same transaction and force `IERC20(this).totalSupply()` apart from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, breaking the invariant that an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit has no cap on the MGP it pays out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() computes vlMGPAmount = _amount * rewardRatio / DENOMINATOR with no per-user limit, no global budget and no remaining-balance check, so a single caller sizing _amount large enough claims the entire MGP balance of the contract as locked vlMGP. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: an incentive budget must be bounded per caller and in aggregate, and must not be drainable in one transaction; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls convertAllWom on WombatStaking in the same transaction, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
