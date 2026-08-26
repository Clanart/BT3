# Q3144: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
In wombat/WombatBribeManager.sol, vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Starting from a state where the pool the attacker voted for has been deactivated so unvote reverts, can an unprivileged EOA use `vote(address[] _lps, int256[] _deltas)` to leave `delegatedPool votes` inconsistent with `totalVlMgpInVote`, violating the invariant that the vote ceiling must hold at every point of the update, not only on the net result and extracting Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Foundry fork test against the deployed pool: set up the pool the attacker voted for has been deactivated so unvote reverts, snapshot `delegatedPool votes` and `totalVlMgpInVote`, run the attacker's `vote(address[] _lps, int256[] _deltas)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
