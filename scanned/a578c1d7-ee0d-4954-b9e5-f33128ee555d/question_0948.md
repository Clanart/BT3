# Q0948: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
rewards/BribeRewardPool.sol - withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Can an unprivileged attacker controlling the negative delta and whether the claim leg runs, under the attacker votes and casts inside one transaction through voteAndCast, exploit this through `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to break the reconciliation between `_balances[account]` and `totalSupply` and the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow, yielding Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence atomically under the attacker votes and casts inside one transaction through voteAndCast, asserting at the end that `_balances[account]` still equals `totalSupply` and the PoC's balance delta is non-positive.
