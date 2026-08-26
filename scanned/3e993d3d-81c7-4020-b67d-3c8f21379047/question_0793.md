# Q0793: BribeRewardPool.withdrawFor - share ledger tracks votes rather than transferred value

## Question
rewards/BribeRewardPool.sol: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. With the negative delta and whether the claim leg runs under attacker control and the attacker votes and casts inside one transaction through voteAndCast, can an unprivileged caller sequence `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`: constrain the setup so that the attacker votes and casts inside one transaction through voteAndCast, fuzz the attacker inputs (the negative delta and whether the claim leg runs), and assert after every call that a reward-share ledger must be backed by value that is actually committed and costly to move.
