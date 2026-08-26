# Q0521: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
Consider wombat/ArbWomUp.sol, where the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Assuming the caller sizes _amount to cross several tier boundaries at once, can an unprivileged attacker turn this into a divergence between `accumulated = _amount + userWOMDeposited[account]` and `the tier boundary crossed` via `incentiveDeposit(uint256 _amount)`, breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the caller sizes _amount to cross several tier boundaries at once.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated) under the caller sizes _amount to cross several tier boundaries at once, asserting on every row that an incentive pot must not be fully claimable by a single actor in one transaction.
