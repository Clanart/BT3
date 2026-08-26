# Q2003: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
In rewards/BribeRewardPool.sol, withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Starting from a state where the bribe token registered for this gauge charges a transfer fee, can an unprivileged EOA use `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `userRewardPerTokenPaid[_rewardToken][account]`, violating the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow and extracting Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` sequence atomically under the bribe token registered for this gauge charges a transfer fee, asserting at the end that `rewards[_rewardToken].rewardPerTokenStored` still equals `userRewardPerTokenPaid[_rewardToken][account]` and the PoC's balance delta is non-positive.
