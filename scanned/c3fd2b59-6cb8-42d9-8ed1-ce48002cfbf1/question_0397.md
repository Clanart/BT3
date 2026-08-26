# Q0397: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
wombat/ArbWomUp.sol: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the contract has just been topped up with USDT, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `rewardAmount / DENOMINATOR` and `claimedReward[account]` no longer reconcile, violating the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: the contract has just been topped up with USDT.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the contract has just been topped up with USDT, then assert `rewardAmount / DENOMINATOR` and `claimedReward[account]` end identical in both runs.
