# Q1957: BribeRewardPool.withdrawFor - queued backlog while totalSupply is zero

## Question
In rewards/BribeRewardPool.sol, _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Starting from a state where the bribe token registered for this gauge charges a transfer fee, can an unprivileged EOA use `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` to leave `_balances[account]` inconsistent with `totalSupply`, violating the invariant that a backlog accrued with no voters must not be assignable to a single one-block voter and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/BribeRewardPool.sol -> `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote` (mechanism: queued backlog while totalSupply is zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the negative delta and whether the claim leg runs
- Exploit idea: _provisionReward() accumulates into queuedRewards whenever totalSupply is zero and releases the entire backlog on the next provision, so the first voter after a quiet period absorbs it. Precondition: the bribe token registered for this gauge charges a transfer fee.
- Invariant to test: a backlog accrued with no voters must not be assignable to a single one-block voter; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the bribe token registered for this gauge charges a transfer fee, have the attacker run `withdrawFor(address _for, uint256 _amount, bool claim) via WombatBribeManager.vote and unvote`, then assert the victim's claimable value and the `_balances[account]` versus `totalSupply` relation are unchanged by the attacker's transaction.
