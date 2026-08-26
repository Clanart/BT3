# Q0328: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
In rewards/BribeRewardPool.sol, withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Can an unprivileged attacker reach this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` while a large bribe for the gauge is pending and no cast has run yet, and drive `rewards[_rewardToken].queuedRewards` out of agreement with `totalSupply at the moment of the flush` - breaking the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow - for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under a large bribe for the gauge is pending and no cast has run yet, asserting on every row that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow.
