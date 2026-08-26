# Q1448: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
wombat/ArbWomUp.sol - usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller has already claimed most of their tier entitlement, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `rewardAmount / DENOMINATOR` and `claimedReward[account]` and the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `incentiveDeposit(uint256 _amount)`: constrain the setup so that the caller has already claimed most of their tier entitlement, fuzz the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated), and assert after every call that the total reward for a given cumulative deposit must be independent of how the deposits were split.
