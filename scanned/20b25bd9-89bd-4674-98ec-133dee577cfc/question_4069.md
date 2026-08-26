# Q4069: BribeRewardPool.stakeFor - queued backlog while totalSupply is zero

## Question
Consider rewards/BribeRewardPool.sol, where _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Assuming the stakingToken fixed at construction has different decimals from vlMGP, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`, breaking the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and producing Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the delta and the beneficiary, both chosen by the voter calling vote
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the stakingToken fixed at construction has different decimals from vlMGP.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `stakeFor(address _for, uint256 _amount) via WombatBribeManager.vote`: constrain the setup so that the stakingToken fixed at construction has different decimals from vlMGP, fuzz the attacker inputs (the delta and the beneficiary, both chosen by the voter calling vote), and assert after every call that a backlog accrued with no voters must not be assignable to a single one-block voter.
