# Q1201: ArbWomUp2.incentiveDeposit - the incentive pot is drainable by one oversized deposit

## Question
Note that in wombat/ArbWomUp2.sol, the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` under the caller splits the deposit across several addresses and force `claimedReward[account]` apart from `userWOMDeposited[account]`, breaking the invariant that an incentive pot must not be fully claimable by a single actor in one transaction for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the incentive pot is drainable by one oversized deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: the tier walk has no per-transaction ceiling and the only bound is the contract's balance, so a single caller can take the entire remaining incentive. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: an incentive pot must not be fully claimable by a single actor in one transaction; concretely, `claimedReward[account]` must stay reconciled with `userWOMDeposited[account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens) under the caller splits the deposit across several addresses, asserting on every row that an incentive pot must not be fully claimable by a single actor in one transaction.
