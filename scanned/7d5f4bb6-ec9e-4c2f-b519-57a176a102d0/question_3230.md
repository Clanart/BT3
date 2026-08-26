# Q3230: BribeRewardPool.withdrawFor - withdrawFor underflows and traps the vote

## Question
Note that in rewards/BribeRewardPool.sol, withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Can an attacker holding only tokens bought on market reach it via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` under the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor and force `_balances[account]` apart from `totalSupply`, breaking the invariant that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow for Critical - Permanent freezing of funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: withdrawFor underflows and traps the vote)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: withdrawFor() subtracts from totalSupply and _balances[_for] with no floor, so any divergence between WombatBribeManager's userVotedForPoolInVlmgp and this ledger makes unvote revert and the vote unreleasable. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that the vote ledger and the rewarder ledger must be provably equal so an exit can never underflow.
