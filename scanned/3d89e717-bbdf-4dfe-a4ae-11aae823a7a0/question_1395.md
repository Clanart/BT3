# Q1395: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Does `incentiveDeposit(uint256 _amount)` let an unprivileged caller exploit that under the caller has already claimed most of their tier entitlement, so that `accumulated = _amount + userWOMDeposited[account]` diverges from `the tier boundary crossed`, the invariant that a token transfer on a payout path must be checked is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller has already claimed most of their tier entitlement, then assert `accumulated = _amount + userWOMDeposited[account]` and `the tier boundary crossed` end identical in both runs.
