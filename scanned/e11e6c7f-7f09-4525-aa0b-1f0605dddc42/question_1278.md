# Q1278: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
Note that in rewards/BribeRewardPool.sol, WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Can an attacker holding only tokens bought on market reach it via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` under totalSupply is zero because every voter has unvoted and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that bribe share must be weighted by time committed before the bribe arrived for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote) under totalSupply is zero because every voter has unvoted, asserting on every row that bribe share must be weighted by time committed before the bribe arrived.
