# Q0078: WombatBribeManager.vote - the vote ceiling is only checked on the net total

## Question
wombat/WombatBribeManager.sol: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. With every lp address and every signed delta, including duplicates and offsetting positive and negative entries under attacker control and a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, can an unprivileged caller sequence `vote(address[] _lps, int256[] _deltas)` so that `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` no longer reconcile, violating the invariant that the vote ceiling must hold at every point of the update, not only on the net result and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `vote(address[] _lps, int256[] _deltas)` (mechanism: the vote ceiling is only checked on the net total)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `vote(address[] _lps, int256[] _deltas)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every lp address and every signed delta, including duplicates and offsetting positive and negative entries
- Exploit idea: vote() accumulates totalUserVote across the whole array and only compares userTotalVotedInVlmgp[msg.sender] against getUserVotable(msg.sender) once, after the loop, so intermediate states inside the loop are never bounded. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: the vote ceiling must hold at every point of the update, not only on the net result; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `vote(address[] _lps, int256[] _deltas)` sequence atomically under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, asserting at the end that `targetVote computed in castVotes` still equals `totalVotes() from veWom.balanceOf(wombatStaking)` and the PoC's balance delta is non-positive.
