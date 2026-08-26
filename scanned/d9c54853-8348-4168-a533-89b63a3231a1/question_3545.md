# Q3545: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
Note that in wombat/mWOM.sol, because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, bool _stake)` under the veWOM mint returns less than the WOM supplied because of the lockDays curve and force `IERC20(this).totalSupply()` apart from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, breaking the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under the veWOM mint returns less than the WOM supplied because of the lockDays curve, asserting at the end that `IERC20(this).totalSupply()` still equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the PoC's balance delta is non-positive.
