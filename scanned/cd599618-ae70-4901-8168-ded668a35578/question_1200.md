# Q1200: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
Consider wombat/ArbWomUp.sol, where incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Assuming userWOMDeposited is still zero for the caller, can an unprivileged attacker turn this into a divergence between `rewardTier[i]` and `rewardMultiplier[i-1]` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that a token transfer on a payout path must be checked and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under userWOMDeposited is still zero for the caller, then assert `rewardTier[i]` and `rewardMultiplier[i-1]` end identical in both runs.
