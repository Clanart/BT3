# Q4320: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Does `claimAllBribes(address _for)` let an unprivileged caller exploit that under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, so that `earnedRewards reported by claimAllBribes` diverges from `the tokens actually transferred by getReward`, the invariant that the amounts reported by a claim must cover every token the claim actually moved is broken, and the result is High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, have the attacker run `claimAllBribes(address _for)`, then assert the victim's claimable value and the `earnedRewards reported by claimAllBribes` versus `the tokens actually transferred by getReward` relation are unchanged by the attacker's transaction.
