# Q0646: ArbWomUp2.incentiveDeposit - a redundant zero guard hides the real entry condition

## Question
wombat/ArbWomUp2.sol: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the contract's BUSD balance is below the tier reward earned, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `rewardToSend` and `IERC20(busd).balanceOf(address(this))` no longer reconcile, violating the invariant that a guard on an entry path must have exactly one behaviour and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: a redundant zero guard hides the real entry condition)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the modifier already rejects a zero amount and the body then returns early on the same condition, so the two guards disagree about whether a zero deposit reverts or silently succeeds. Precondition: the contract's BUSD balance is below the tier reward earned.
- Invariant to test: a guard on an entry path must have exactly one behaviour; concretely, `rewardToSend` must stay reconciled with `IERC20(busd).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the contract's BUSD balance is below the tier reward earned, snapshot `rewardToSend` and `IERC20(busd).balanceOf(address(this))`, run the attacker's `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
