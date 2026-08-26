# Q5769: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
Note that in wombat/WombatBribeManager.sol, claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Can an attacker holding only tokens bought on market reach it via `claimAllBribes(address _for)` under the bribe contract for the pool registers more than one reward token and force `targetVote computed in castVotes` apart from `totalVotes() from veWom.balanceOf(wombatStaking)`, breaking the invariant that the amounts reported by a claim must cover every token the claim actually moved for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the bribe contract for the pool registers more than one reward token, then assert `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` end identical in both runs.
