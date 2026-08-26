# Q0018: BribeRewardPool.stakeFor - share ledger tracks votes rather than transferred value

## Question
rewards/BribeRewardPool.sol: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. With the delta and the beneficiary, both chosen by the voter calling vote under attacker control and a large bribe for the gauge is pending and no cast has run yet, can an unprivileged caller sequence `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` so that `_balances[account]` and `totalSupply` no longer reconcile, violating the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: a large bribe for the gauge is pending and no cast has run yet.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe for the gauge is pending and no cast has run yet, then assert `_balances[account]` and `totalSupply` end identical in both runs.
