# Q0638: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
In rewards/BribeRewardPool.sol, stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Does `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` let an unprivileged caller exploit that under the attacker votes and casts inside one transaction through voteAndCast, so that `totalSupply` diverges from `the sum of userVotedForPoolInVlmgp over all voters for this pool`, the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the attacker votes and casts inside one transaction through voteAndCast.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker votes and casts inside one transaction through voteAndCast, call `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, and assert `totalSupply` equals `the sum of userVotedForPoolInVlmgp over all voters for this pool` and that no account can withdraw more than it put in.
