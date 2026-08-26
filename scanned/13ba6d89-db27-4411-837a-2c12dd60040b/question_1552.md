# Q1552: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
wombat/ArbWomUp.sol: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. With _amount with no per-user or global cap, and how many times the call is repeated under attacker control and the USDT implementation returns false rather than reverting on failure, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount)` so that `accumulated = _amount + userWOMDeposited[account]` and `the tier boundary crossed` no longer reconcile, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the USDT implementation returns false rather than reverting on failure.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `accumulated = _amount + userWOMDeposited[account]` must stay reconciled with `the tier boundary crossed`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount with no per-user or global cap, and how many times the call is repeated) under the USDT implementation returns false rather than reverting on failure, asserting on every row that an incentive pot must not be fully claimable by a single actor in one transaction.
