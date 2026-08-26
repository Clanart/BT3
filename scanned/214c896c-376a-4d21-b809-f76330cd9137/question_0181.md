# Q0181: ArbWomUp2.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
wombat/ArbWomUp2.sol: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller sets _minMGPRec to zero and sandwiches the router pair, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `claimedReward[account]` and `userWOMDeposited[account]` no longer reconcile, violating the invariant that a token transfer on a payout path must be checked and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Precondition: the caller sets _minMGPRec to zero and sandwiches the router pair.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller sets _minMGPRec to zero and sandwiches the router pair, then assert `claimedReward[account]` and `userWOMDeposited[account]` end identical in both runs.
