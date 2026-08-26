# Q4114: BribeRewardPool.withdrawFor - share ledger tracks votes rather than transferred value

## Question
Consider rewards/BribeRewardPool.sol, where stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Assuming the stakingToken fixed at construction has different decimals from vlMGP, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, breaking the invariant that a reward-share ledger must be backed by value that is actually committed and costly to move and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: share ledger tracks votes rather than transferred value)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: stakeFor() and withdrawFor() move totalSupply and _balances purely from vote deltas with no token ever changing hands, so a voter can create and destroy bribe share at will inside one transaction. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: a reward-share ledger must be backed by value that is actually committed and costly to move; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (the negative delta and whether the claim leg runs) under the stakingToken fixed at construction has different decimals from vlMGP, asserting on every row that a reward-share ledger must be backed by value that is actually committed and costly to move.
