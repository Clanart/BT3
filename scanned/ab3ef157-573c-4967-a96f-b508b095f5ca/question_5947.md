# Q5947: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
Note that in wombat/WombatBribeManager.sol, vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Can an attacker holding only tokens bought on market reach it via `vote(address[] _lps, int256[] _deltas)` under a keeper castVotes transaction is pending in the mempool and force `delegatedPool votes` apart from `totalVlMgpInVote`, breaking the invariant that the vote ceiling must hold at every point of the update, not only on the net result for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: a keeper castVotes transaction is pending in the mempool.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish a keeper castVotes transaction is pending in the mempool, have the attacker run `vote(address[] _lps, int256[] _deltas)`, then assert the victim's claimable value and the `delegatedPool votes` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
