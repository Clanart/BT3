# Q1818: DelegateVoteRewardPool.getReward - tokens beyond the first bribe token arrive unaccounted

## Question
In rewards/DelegateVoteRewardPool.sol, claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Starting from a state where protocolFee is zero so the whole reported amount is queued, can an unprivileged EOA use `getReward(address _for)` to leave `rewards[_rewardToken].rewardPerTokenStored` inconsistent with `totalSupply of the delegate pool`, violating the invariant that every token that arrives must be routed into the index or returned and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/DelegateVoteRewardPool.sol -> `getReward(address _for)` (mechanism: tokens beyond the first bribe token arrive unaccounted)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the settlement timing
- Exploit idea: claimAllBribes reports only rewardTokens()[0] per pool while getReward transfers every registered bribe token, so the extra tokens land on this contract without ever being queued and become unclaimable. Precondition: protocolFee is zero so the whole reported amount is queued.
- Invariant to test: every token that arrives must be routed into the index or returned; concretely, `rewards[_rewardToken].rewardPerTokenStored` must stay reconciled with `totalSupply of the delegate pool`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under protocolFee is zero so the whole reported amount is queued, then assert `rewards[_rewardToken].rewardPerTokenStored` and `totalSupply of the delegate pool` end identical in both runs.
