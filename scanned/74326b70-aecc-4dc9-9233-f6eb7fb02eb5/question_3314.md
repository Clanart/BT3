# Q3314: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
In wombat/WombatBribeManager.sol, VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Does `unvote(address _lp)` let an unprivileged caller exploit that under the pool the attacker voted for has been deactivated so unvote reverts, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a governance commitment must never be able to become permanently unreleasable is broken, and the result is Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the pool the attacker voted for has been deactivated so unvote reverts, call `unvote(address _lp)`, and assert `delegatedPool votes` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
