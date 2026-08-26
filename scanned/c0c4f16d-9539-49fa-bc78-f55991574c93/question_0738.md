# Q0738: ArbWomUp.incentiveDeposit - the tier bonus is drained by a single oversized deposit

## Question
In wombat/ArbWomUp.sol, the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Starting from a state where the caller splits the same total deposit across many small calls, can an unprivileged EOA use `incentiveDeposit(uint256 _amount)` to leave `claimedReward[account]` inconsistent with `userWOMDeposited[account]`, violating the invariant that an incentive pot must not be fully claimable by a single actor in one transaction and extracting Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp.sol -> `incentiveDeposit(uint256 _amount)` (mechanism: the tier bonus is drained by a single oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no per-user or global cap, and how many times the call is repeated
- Exploit idea: the tier walk accumulates across every bracket up to the deposit size with no per-transaction ceiling, and the only bound is the contract's USDT balance, so one caller sizing _amount large enough takes the entire incentive pot. Precondition: the caller splits the same total deposit across many small calls.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the caller splits the same total deposit across many small calls, snapshot `claimedReward[account]` and `userWOMDeposited[account]`, run the attacker's `incentiveDeposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
