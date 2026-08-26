# Q0769: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
In wombat/ArbWomUp.sol, incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `rewardAmount / DENOMINATOR` inconsistent with `claimedReward[account]`, violating the invariant that a token transfer on a payout path must be checked and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the caller splits the same total deposit across many small calls, asserting at the end that `rewardAmount / DENOMINATOR` still equals `claimedReward[account]` and the PoC's balance delta is non-positive.
