# Q0986: ArbWomUp.incentiveDeposit - the payout uses a raw transfer whose result is ignored

## Question
wombat/ArbWomUp.sol - incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller splits the same total deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `usdtReward` and `IERC20(usdt).balanceOf(address(this))` and the invariant that a token transfer on a payout path must be checked, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the payout uses a raw transfer whose result is ignored)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: incentiveDeposit() calls IERC20(usdt).transfer(msg.sender, rewardToSend) rather than safeTransfer, so a token that returns false instead of reverting leaves the claimed counter advanced with nothing delivered. Precondition: the caller splits the same total deposit across several addresses.
- Invariant to test: a token transfer on a payout path must be checked; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the caller splits the same total deposit across several addresses, have the attacker run `incentiveDeposit(uint256 _amount)`, then assert the victim's claimable value and the `usdtReward` versus `IERC20(usdt).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
