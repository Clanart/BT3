# Q5933: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
Consider wombat/WombatBribeManager.sol, where claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Assuming the attacker has just cancelled a cooldown so getUserVotable jumped upward, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` via `claimAllBribes(address _for)`, breaking the invariant that the amounts reported by a claim must cover every token the claim actually moved and producing High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker has just cancelled a cooldown so getUserVotable jumped upward, call `claimAllBribes(address _for)`, and assert `poolInfos[lp].isActive` equals `userVotedForPoolInVlmgp[user][lp]` and that no account can withdraw more than it put in.
