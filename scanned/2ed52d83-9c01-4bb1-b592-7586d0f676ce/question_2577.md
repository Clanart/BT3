# Q2577: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
Consider rewards/DelegateVoteRewardPool.sol, where claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Assuming a keeper castVotes transaction that ends in harvestAll is pending in the mempool, can an unprivileged attacker turn this into a divergence between `_balances[account]` and `totalSupply` via `getReward(address _for)`, breaking the invariant that every token that arrives must be routed into the index or returned and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: a keeper castVotes transaction that ends in harvestAll is pending in the mempool.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `_balances[account]` must stay reconciled with `totalSupply`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that a keeper castVotes transaction that ends in harvestAll is pending in the mempool, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that every token that arrives must be routed into the index or returned.
