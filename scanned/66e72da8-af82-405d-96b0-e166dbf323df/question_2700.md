# Q2700: BribeRewardPool.stakeFor - stake credited before the bribe for the epoch is queued

## Question
rewards/BribeRewardPool.sol - WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Can an unprivileged attacker controlling the delta and the beneficiary, both chosen by the voter calling vote, under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, exploit this through `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` to break the reconciliation between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` and the invariant that bribe share must be weighted by time committed before the bribe arrived, yielding Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: stake credited before the bribe for the epoch is queued)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: WombatBribeManager.voteAndCast calls vote(), which runs stakeFor, and then castVotes(), which queues the harvested bribes into rewardPerTokenStored, so balance created in the first half of the transaction earns on the bribe delivered in the second half. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: bribe share must be weighted by time committed before the bribe arrived; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, then assert `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` end identical in both runs.
