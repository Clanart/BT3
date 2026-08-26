# Q5885: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
In wombat/WombatBribeManager.sol, VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an unprivileged attacker reach this through `unvote(address _lp)` while the attacker has just cancelled a cooldown so getUserVotable jumped upward, and drive `poolInfos[lp].isActive` out of agreement with `userVotedForPoolInVlmgp[user][lp]` - breaking the invariant that a governance commitment must never be able to become permanently unreleasable - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the attacker has just cancelled a cooldown so getUserVotable jumped upward.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `unvote(address _lp)` sequence atomically under the attacker has just cancelled a cooldown so getUserVotable jumped upward, asserting at the end that `poolInfos[lp].isActive` still equals `userVotedForPoolInVlmgp[user][lp]` and the PoC's balance delta is non-positive.
