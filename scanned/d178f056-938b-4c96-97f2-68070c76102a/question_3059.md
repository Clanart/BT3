# Q3059: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
Consider rewards/BribeRewardPool.sol, where stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Assuming the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, can an unprivileged attacker turn this into a divergence between `totalSupply` and `the sum of userVotedForPoolInVlmgp over all voters for this pool` via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, breaking the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `totalSupply` must stay reconciled with `the sum of userVotedForPoolInVlmgp over all voters for this pool`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the gauge's pool has been deactivated so unvote reverts before reaching withdrawFor, have the attacker run `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, then assert the victim's claimable value and the `totalSupply` versus `the sum of userVotedForPoolInVlmgp over all voters for this pool` relation are unchanged by the attacker's transaction.
