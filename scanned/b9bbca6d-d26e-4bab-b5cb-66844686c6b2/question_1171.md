# Q1171: ArbWomUp2.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
wombat/ArbWomUp2.sol: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Under the caller splits the deposit across several addresses, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` that leaves `calDoubledCounted(account)` unreconciled with `rewardTier and rewardMultiplier walk`, violates the invariant that a token transfer on a payout path must be checked, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the non-bull branch calls IERC20(busd).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false leaves the claimed counter advanced with nothing delivered. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `calDoubledCounted(account)` must stay reconciled with `rewardTier and rewardMultiplier walk`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller splits the deposit across several addresses, then assert `calDoubledCounted(account)` and `rewardTier and rewardMultiplier walk` end identical in both runs.
