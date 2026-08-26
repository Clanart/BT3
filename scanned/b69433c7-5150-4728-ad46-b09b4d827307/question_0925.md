# Q0925: ArbWomUp2.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
wombat/ArbWomUp2.sol: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and bullBonusRatio is configured well above zero, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `bullBonusRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that a token transfer on a payout path must be checked and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Precondition: bullBonusRatio is configured well above zero.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `bullBonusRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange bullBonusRatio is configured well above zero, call `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`, and assert `bullBonusRatio` equals `DENOMINATOR` and that no account can withdraw more than it put in.
