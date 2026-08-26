# Q0955: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
wombat/ArbWomUp.sol - the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Can an unprivileged attacker controlling _amount with no per-user or global cap, and how many times the call is repeated, under the caller splits the same total deposit across several addresses, exploit this through `incentiveDeposit(uint256 _amount)` to break the reconciliation between `rewardAmount / DENOMINATOR` and `claimedReward[account]` and the invariant that an incentive pot must not be fully claimable by a single actor in one transaction, yielding Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the caller splits the same total deposit across several addresses.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `rewardAmount / DENOMINATOR` must stay reconciled with `claimedReward[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller splits the same total deposit across several addresses, call `incentiveDeposit(uint256 _amount)`, and assert `rewardAmount / DENOMINATOR` equals `claimedReward[account]` and that no account can withdraw more than it put in.
