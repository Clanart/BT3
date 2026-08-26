# Q0916: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Does `getReward(address _for)` let an unprivileged caller exploit that under totalSupply is zero and queuedRewards holds a backlog, so that `_balances[account]` diverges from `totalSupply`, the invariant that every token that arrives must be routed into the index or returned is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: totalSupply is zero and queuedRewards holds a backlog.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange totalSupply is zero and queuedRewards holds a backlog, call `getReward(address _for)`, and assert `_balances[account]` equals `totalSupply` and that no account can withdraw more than it put in.
