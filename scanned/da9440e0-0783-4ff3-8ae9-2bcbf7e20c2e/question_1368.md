# Q1368: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
wombat/ArbWomUp.sol: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the caller has already claimed most of their tier entitlement, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `rewardTier[i]` and `rewardMultiplier[i-1]` no longer reconcile, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the caller has already claimed most of their tier entitlement.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardTier[i]` must stay reconciled with `rewardMultiplier[i-1]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the caller has already claimed most of their tier entitlement, then assert `rewardTier[i]` and `rewardMultiplier[i-1]` end identical in both runs.
