# Q4189: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
In rewards/BribeRewardPool.sol, withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Does `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` let an unprivileged caller exploit that under the stakingToken fixed at construction has different decimals from vlMGP, so that `userRewards[_rewardToken][account]` diverges from `earned(account,_rewardToken)`, the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the stakingToken fixed at construction has different decimals from vlMGP, have the attacker run `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, then assert the victim's claimable value and the `userRewards[_rewardToken][account]` versus `earned(account,_rewardToken)` relation are unchanged by the attacker's transaction.
