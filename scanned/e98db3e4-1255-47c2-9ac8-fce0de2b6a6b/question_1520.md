# Q1520: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
In rewards/BribeRewardPool.sol, withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Can an unprivileged attacker reach this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` while totalSupply is zero because every voter has unvoted, and drive `totalSupply` out of agreement with `the sum of userVotedForPoolInVlmgp over all voters for this pool` - breaking the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under totalSupply is zero because every voter has unvoted, then assert `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` end identical in both runs.
