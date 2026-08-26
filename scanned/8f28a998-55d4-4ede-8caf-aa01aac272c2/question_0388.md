# Q0388: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
wombat/WombatBribeManager.sol: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `unvote(address _lp)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that a governance commitment must never be able to become permanently unreleasable, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, have the attacker run `unvote(address _lp)`, then assert the victim's claimable value and the `targetVote computed in castVotes` versus `totalVotes() from veWom.balanceOf(wombatStaking)` relation are unchanged by the attacker's transaction.
