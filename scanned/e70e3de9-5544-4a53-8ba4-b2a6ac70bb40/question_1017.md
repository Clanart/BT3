# Q1017: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
wombat/ArbWomUp.sol - getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller splits the same total deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `rewardTier[i]` and `rewardMultiplier[i-1]` and the invariant that a tier accessor must handle every accumulation value without reverting, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the caller splits the same total deposit across several addresses.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the caller splits the same total deposit across several addresses, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that a tier accessor must handle every accumulation value without reverting.
