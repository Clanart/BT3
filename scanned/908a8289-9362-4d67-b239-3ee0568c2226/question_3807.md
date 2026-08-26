# Q3807: BribeRewardPool.withdrawFor - share ledger tracks votes rather than transferred value

## Question
rewards/BribeRewardPool.sol: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Under the victim has a large unsettled bribe balance, is there an unprivileged sequence of `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` that leaves `rewards[_rewardToken].queuedRewards` unreconciled with `totalSupply at the moment of the flush`, violates the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the victim has a large unsettled bribe balance.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `rewards[_rewardToken].queuedRewards` must stay reconciled with `totalSupply at the moment of the flush`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under the victim has a large unsettled bribe balance, asserting on every row that a reward-share ledger must be backed by value that is actually committed and costly to move.
