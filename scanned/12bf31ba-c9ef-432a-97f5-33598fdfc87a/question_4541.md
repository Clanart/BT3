# Q4541: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
In wombat/WombatBribeManager.sol, VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an unprivileged attacker reach this through `unvote(address _lp)` while delegatedPool is unset so the delegate legs are skipped, and drive `userTotalVotedInVlmgp[msg.sender]` out of agreement with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` - breaking the invariant that a governance commitment must never be able to become permanently unreleasable - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: delegatedPool is unset so the delegate legs are skipped.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish delegatedPool is unset so the delegate legs are skipped, have the attacker run `unvote(address _lp)`, then assert the victim's claimable value and the `userTotalVotedInVlmgp[msg.sender]` versus `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` relation are unchanged by the attacker's transaction.
