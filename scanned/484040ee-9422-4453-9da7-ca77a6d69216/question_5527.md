# Q5527: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
Consider wombat/WombatBribeManager.sol, where vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Assuming the attacker passes an lp address that was never registered in poolInfos, can an unprivileged attacker turn this into a divergence between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` via `vote(address[] _lps, int256[] _deltas)`, breaking the invariant that the vote ceiling must hold at every point of the update, not only on the net result and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes an lp address that was never registered in poolInfos, call `vote(address[] _lps, int256[] _deltas)`, and assert `userVotedForPoolInVlmgp[user][lp]` equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and that no account can withdraw more than it put in.
