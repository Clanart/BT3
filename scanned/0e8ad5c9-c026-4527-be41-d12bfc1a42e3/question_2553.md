# Q2553: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
wombat/WombatBribeManager.sol - VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an unprivileged attacker controlling _lp and the moment the whole position on that pool is released, under the attacker votes in the block immediately before a known keeper cast, exploit this through `unvote(address _lp)` to break the reconciliation between `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` and the invariant that a governance commitment must never be able to become permanently unreleasable, yielding Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Invariant/fuzz run over `unvote(address _lp)`: constrain the setup so that the attacker votes in the block immediately before a known keeper cast, fuzz the attacker inputs (_lp and the moment the whole position on that pool is released), and assert after every call that a governance commitment must never be able to become permanently unreleasable.
