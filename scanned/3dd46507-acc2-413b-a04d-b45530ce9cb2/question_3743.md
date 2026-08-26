# Q3743: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
rewards/BribeRewardPool.sol: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Under the victim has a large unsettled bribe balance, is there an unprivileged sequence of `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `totalSupply at the moment of the flush`, violates the invariant that bribe share must be weighted by time committed before the bribe arrived, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote) under the victim has a large unsettled bribe balance, asserting on every row that bribe share must be weighted by time committed before the bribe arrived.
