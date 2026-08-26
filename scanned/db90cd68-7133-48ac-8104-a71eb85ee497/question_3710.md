# Q3710: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `claimAllBribes(address _for)` to leave `delegatedPool votes` inconsistent with `totalVlMgpInVote`, violating the invariant that the amounts reported by a claim must cover every token the claim actually moved and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the pool the attacker voted for has been deactivated so unvote reverts, then assert `delegatedPool votes` and `totalVlMgpInVote` end identical in both runs.
