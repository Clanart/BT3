# Q2874: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
Consider rewards/BribeRewardPool.sol, where withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Assuming the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, can an unprivileged attacker turn this into a divergence between `rewards[_rewardToken].queuedRewards` and `totalSupply at the moment of the flush` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow and producing Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that the operator WombatBribeManager has a lower userVotedForPoolInVlmgp than this pool's _balances, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow.
