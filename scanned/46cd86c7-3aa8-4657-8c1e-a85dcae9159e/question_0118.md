# Q0118: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `rewardTier[i]` out of agreement with `rewardMultiplier[i-1]` - breaking the invariant that a token transfer on a payout path must be checked - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the contract's USDT balance is below the tier reward the deposit earned, call `incentiveDeposit(uint256 _amount)`, and assert `rewardTier[i]` equals `rewardMultiplier[i-1]` and that no account can withdraw more than it put in.
