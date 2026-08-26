# Q0583: ArbWomUp.incentiveDeposit - the tier walk underflows at the bottom bracket

## Question
Consider wombat/ArbWomUp.sol, where getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `rewardAmount / DENOMINATOR` and `claimedReward[account]` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that a tier accessor must handle every accumulation value without reverting and producing Critical - Permanent freezing of funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier walk underflows at the bottom bracket)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: getRewardAmount() ends with rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1], which underflows whenever accumulated sits below rewardTier[0], making the whole entry path revert. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: a tier accessor must handle every accumulation value without reverting; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller sizes _amount to cross several tier boundaries at once, snapshot `rewardAmount / DENOMINATOR` and `claimedReward[account]`, run the attacker's `incentiveDeposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
