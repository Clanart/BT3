# Q4984: WombatBribeManager.vote - castVotes writes lastCastTime before the external voter call

## Question
In wombat/WombatBribeManager.sol, lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Starting from a state where the attacker passes the same lp address several times in one array, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `earnedRewards reported by claimAllBribes` inconsistent with `the tokens actually transferred by getReward`, violating the invariant that a cadence marker must only advance once the operation it marks has completed and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the attacker passes the same lp address several times in one array, asserting at the end that `earnedRewards reported by claimAllBribes` still equals `the tokens actually transferred by getReward` and the PoC's balance delta is non-positive.
