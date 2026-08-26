# Q3016: mWOM.incentiveDeposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol - the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under the attacker calls convertAllWom on WombatStaking in the same transaction, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` and the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, bool _stake)` sequence atomically under the attacker calls convertAllWom on WombatStaking in the same transaction, asserting at the end that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
