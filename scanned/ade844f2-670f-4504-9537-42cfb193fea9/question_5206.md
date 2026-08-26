# Q5206: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Starting from a state where the attacker passes the same lp address several times in one array, can an unprivileged EOA use `claimAllBribes(address _for)` to leave `poolInfos[lp].totalVoteInVlmgp` inconsistent with `totalVlMgpInVote`, violating the invariant that the amounts reported by a claim must cover every token the claim actually moved and extracting High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claimAllBribes(address _for)` sequence atomically under the attacker passes the same lp address several times in one array, asserting at the end that `poolInfos[lp].totalVoteInVlmgp` still equals `totalVlMgpInVote` and the PoC's balance delta is non-positive.
