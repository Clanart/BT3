# Q0335: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
wombat/ArbWomUp.sol: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the contract has just been topped up with USDT, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `accumulated = _amount + userWOMDeposited[account]` and `the tier boundary crossed` no longer reconcile, violating the invariant that a token transfer on a payout path must be checked and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: the contract has just been topped up with USDT.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the contract has just been topped up with USDT, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that a token transfer on a payout path must be checked.
