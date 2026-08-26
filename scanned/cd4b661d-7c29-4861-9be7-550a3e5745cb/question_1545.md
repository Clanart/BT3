# Q1545: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
Consider rewards/DelegateVoteRewardPool.sol, where claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Assuming protocolFee is non-zero and feeCollector is set, can an unprivileged attacker turn this into a divergence between `earnedRewards returned by claimAllBribes` and `IERC20(rewardToken).balanceOf(address(this))` via `getReward(address _for)`, breaking the invariant that every token that arrives must be routed into the index or returned and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: protocolFee is non-zero and feeCollector is set.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `earnedRewards returned by claimAllBribes` must stay reconciled with `IERC20(rewardToken).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Invariant/fuzz run over `getReward(address _for)`: constrain the setup so that protocolFee is non-zero and feeCollector is set, fuzz the attacker inputs (_for (any victim) and the settlement timing), and assert after every call that every token that arrives must be routed into the index or returned.
