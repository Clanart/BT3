# Q5721: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
wombat/WombatBribeManager.sol - VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an unprivileged attacker controlling _lp and the moment the whole position on that pool is released, under the bribe contract for the pool registers more than one reward token, exploit this through `unvote(address _lp)` to break the reconciliation between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` and the invariant that a governance commitment must never be able to become permanently unreleasable, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Table test over the boundary values of the attacker inputs (_lp and the moment the whole position on that pool is released) under the bribe contract for the pool registers more than one reward token, asserting on every row that a governance commitment must never be able to become permanently unreleasable.
