# Q3948: WombatBribeManager.vote - stakeFor and withdrawFor mirror votes into a rewarder with no share cap

## Question
wombat/WombatBribeManager.sol: vote() calls IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta) and withdrawFor on the negative branch, so the rewarder's totalSupply tracks vote deltas rather than any transferred value and can be moved freely inside one transaction. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `earnedRewards reported by claimAllBribes` and `the tokens actually transferred by getReward` no longer reconcile, violating the invariant that a reward-share ledger must track committed value, not a figure the beneficiary can move at will in one block and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: stakeFor and withdrawFor mirror votes into a rewarder with no share cap)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() calls IBribeRewardPool(pool.rewarder).stakeFor(msg.sender, delta) and withdrawFor on the negative branch, so the rewarder's totalSupply tracks vote deltas rather than any transferred value and can be moved freely inside one transaction. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a reward-share ledger must track committed value, not a figure the beneficiary can move at will in one block; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
