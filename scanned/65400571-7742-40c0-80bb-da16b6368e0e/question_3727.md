# Q3727: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
Note that in rewards/BribeRewardPool.sol, stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Can an attacker holding only tokens bought on market reach it via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` under the victim has a large unsettled bribe balance and force `userRewards[_rewardToken][account]` apart from `earned(account,_rewardToken)`, breaking the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move for Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `userRewards[_rewardToken][account]` must stay reconciled with `earned(account,_rewardToken)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the victim has a large unsettled bribe balance, then assert `userRewards[_rewardToken][account]` and `earned(account,_rewardToken)` end identical in both runs.
