# Q2323: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
Note that in wombat/WombatBribeManager.sol, vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under the attacker votes in the block immediately before a known keeper cast and force `poolInfos[lp].isActive` apart from `userVotedForPoolInVlmgp[user][lp]`, breaking the invariant that the vote ceiling must hold at every point of the update, not only on the net result for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under the attacker votes in the block immediately before a known keeper cast, asserting at the end that `poolInfos[lp].isActive` still equals `userVotedForPoolInVlmgp[user][lp]` and the PoC's balance delta is non-positive.
