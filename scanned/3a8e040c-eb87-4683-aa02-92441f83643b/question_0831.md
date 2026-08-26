# Q0831: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
In wombat/ArbWomUp.sol, usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `rewardTier[i]` inconsistent with `rewardMultiplier[i-1]`, violating the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller splits the same total deposit across many small calls, have the attacker run `incentiveDeposit(uint256 _amount)`, then assert the victim's claimable value and the `rewardTier[i]` versus `rewardMultiplier[i-1]` relation are unchanged by the attacker's transaction.
