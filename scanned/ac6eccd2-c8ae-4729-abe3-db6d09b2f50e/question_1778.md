# Q1778: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
In wombat/ArbWomUp2.sol, the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Does `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` let an unprivileged caller exploit that under userWOMDeposited is still zero for the caller, so that `rewardToSend` diverges from `IERC20(busd).balanceOf(address(this))`, the invariant that a guard on an entry path must have exactly one behaviour is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under userWOMDeposited is still zero for the caller, then assert `rewardToSend` and `IERC20(busd).balanceOf(address(this))` end identical in both runs.
