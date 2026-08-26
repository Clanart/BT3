# Q1796: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
rewards/BribeRewardPool.sol - WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Can an unprivileged attacker controlling the delta and the beneficiary, both chosen by the voter calling vote, under the bribe token registered for this gauge charges a transfer fee, exploit this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` to break the reconciliation between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` and the invariant that bribe share must be weighted by time committed before the bribe arrived, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the bribe token registered for this gauge charges a transfer fee, snapshot `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush`, run the attacker's `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
