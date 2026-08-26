# Q1257: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
In wombat/ArbWomUp.sol, usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Does `incentiveDeposit(uint256 _amount)` let an unprivileged caller exploit that under userWOMDeposited is still zero for the caller, so that `claimedReward[account]` diverges from `userWOMDeposited[account]`, the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: userWOMDeposited is still zero for the caller.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated) under userWOMDeposited is still zero for the caller, asserting on every row that the total reward for a given cumulative deposit must be independent of how the deposits were split.
