# Q1518: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
wombat/WombatBribeManager.sol: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and the attacker locks vlMGP, votes and casts inside a single transaction, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` no longer reconcile, violating the invariant that a cadence marker must only advance once the operation it marks has completed and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks vlMGP, votes and casts inside a single transaction, have the attacker run `vote(address[] _lps, int256[] _deltas)`, then assert the victim's claimable value and the `userVotedForPoolInVlmgp[user][lp]` versus `IBribeRewardPool(pool.rewarder).balanceOf(user)` relation are unchanged by the attacker's transaction.
