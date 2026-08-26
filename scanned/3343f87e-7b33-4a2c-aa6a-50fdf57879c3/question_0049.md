# Q0049: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
rewards/BribeRewardPool.sol: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and a large bribe for the gauge is pending and no cast has run yet, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` no longer reconcile, violating the invariant that bribe share must be weighted by time committed before the bribe arrived and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a large bribe for the gauge is pending and no cast has run yet, snapshot `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool`, run the attacker's `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
