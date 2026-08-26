# Q2463: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
rewards/BribeRewardPool.sol: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Under the bribe token has begun reverting on transfer, is there an unprivileged sequence of `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` that leaves `userRewards[_rewardToken][account]` unreconciled with `earned(account,_rewardToken)`, violates the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the bribe token has begun reverting on transfer.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe token has begun reverting on transfer, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
