# Q2623: ArbWomUp3.incentiveDeposit - the mode two doubling is applied after the balance cap

## Question
wombat/ArbWomUp3.sol: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Under the caller crosses several tier boundaries in one deposit, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` that leaves `bracketRewarded` unreconciled with `calDoubledCounted(account)`, violates the invariant that a bonus multiplier must be applied before, not after, the check that the contract can pay, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the mode two doubling is applied after the balance cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: getRewardAmount() already clamps its result to IERC20(mgp).balanceOf(address(this)), and incentiveDeposit then multiplies by two when _mode is 2, so the figure passed to lockFor can exceed the MGP the contract holds. Precondition: the caller crosses several tier boundaries in one deposit.
- Invariant to test: a bonus multiplier must be applied before, not after, the check that the contract can pay; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer) under the caller crosses several tier boundaries in one deposit, asserting on every row that a bonus multiplier must be applied before, not after, the check that the contract can pay.
