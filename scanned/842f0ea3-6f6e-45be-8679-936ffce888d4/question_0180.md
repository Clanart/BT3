# Q0180: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
In wombat/ArbWomUp.sol, usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Can an unprivileged attacker reach this through `incentiveDeposit(uint256 _amount)` while the contract's USDT balance is below the tier reward the deposit earned, and drive `claimedReward[account]` out of agreement with `userWOMDeposited[account]` - breaking the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split - for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: the contract's USDT balance is below the tier reward the deposit earned.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the contract's USDT balance is below the tier reward the deposit earned, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that the total reward for a given cumulative deposit must be independent of how the deposits were split.
