# Q0614: ArbWomUp.incentiveDeposit - claimedReward is subtracted after the tier walk rather than inside it

## Question
Consider wombat/ArbWomUp.sol, where usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `usdtReward` and `IERC20(usdt).balanceOf(address(this))` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that the total reward for a given cumulative deposit must be independent of how the deposits were split and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: claimedReward is subtracted after the tier walk rather than inside it)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: usdtReward is (rewardAmount / DENOMINATOR) - claimedReward[_account], so the division truncates before the subtraction and repeated small deposits round differently from one large deposit. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: the total reward for a given cumulative deposit must be independent of how the deposits were split; concretely, `usdtReward` must stay reconciled with `IERC20(usdt).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount)` sequence atomically under the caller sizes _amount to cross several tier boundaries at once, asserting at the end that `usdtReward` still equals `IERC20(usdt).balanceOf(address(this))` and the PoC's balance delta is non-positive.
