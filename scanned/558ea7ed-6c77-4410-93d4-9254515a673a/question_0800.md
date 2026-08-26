# Q0800: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
In wombat/ArbWomUp.sol, getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `usdtReward` inconsistent with `IERC20(usdt).balanceOf(address(this))`, violating the invariant that a tier accessor must handle every accumulation value without reverting and extracting Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller splits the same total deposit across many small calls, call `incentiveDeposit(uint256 _amount)`, and assert `usdtReward` equals `IERC20(usdt).balanceOf(address(this))` and that no account can withdraw more than it put in.
