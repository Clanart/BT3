# Q1131: WombatBribeManager.claimAllBribes - claimAllBribes only accounts the first bribe token per pool

## Question
In wombat/WombatBribeManager.sol, claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Can an unprivileged attacker reach this through `claimAllBribes(address _for)` while a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, and drive `targetVote computed in castVotes` out of agreement with `totalVotes() from veWom.balanceOf(wombatStaking)` - breaking the invariant that the amounts reported by a claim must cover every token the claim actually moved - for High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimAllBribes(address _for)` (mechanism: claimAllBribes only accounts the first bribe token per pool)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimAllBribes(address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the block at which every pool rewarder is settled for them
- Exploit idea: claimAllBribes() sets rewardTokens[i] = IWombatBribe(bribesContract).rewardTokens()[0] and reports earnedRewards for that token alone, while IBribeRewardPool.getReward transfers every registered reward token, so all tokens beyond index zero are moved without being reported. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: the amounts reported by a claim must cover every token the claim actually moved; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `claimAllBribes(address _for)` sequence atomically under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting at the end that `targetVote computed in castVotes` still equals `totalVotes() from veWom.balanceOf(wombatStaking)` and the PoC's balance delta is non-positive.
