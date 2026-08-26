# Q1250: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
rewards/BribeRewardPool.sol: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and totalSupply is zero because every voter has unvoted, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `rewards[_rewardToken].rewardPerTokenStored` and `userRewardPerTokenPaid[_rewardToken][account]` no longer reconcile, violating the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: totalSupply is zero because every voter has unvoted.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `userRewardPerTokenPaid[_rewardToken][account]`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish totalSupply is zero because every voter has unvoted, have the attacker run `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, then assert the victim's claimable value and the `rewards[_rewardToken].rewardPerTokenStored` versus `userRewardPerTokenPaid[_rewardToken][account]` relation are unchanged by the attacker's transaction.
