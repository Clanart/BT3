# Q3933: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Can an unprivileged attacker reach this through `vote(address[] _lps, int256[] _deltas)` while the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, and drive `poolInfos[lp].isActive` out of agreement with `userVotedForPoolInVlmgp[user][lp]` - breaking the invariant that a cadence marker must only advance once the operation it marks has completed - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, call `vote(address[] _lps, int256[] _deltas)`, and assert `poolInfos[lp].isActive` equals `userVotedForPoolInVlmgp[user][lp]` and that no account can withdraw more than it put in.
