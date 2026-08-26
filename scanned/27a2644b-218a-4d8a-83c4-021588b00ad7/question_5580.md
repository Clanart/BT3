# Q5580: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
wombat/WombatBribeManager.sol: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Under the attacker passes an lp address that was never registered in poolInfos, is there an unprivileged sequence of `unvote(address _lp)` that leaves `userVotedForPoolInVlmgp[user][lp]` unreconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`, violates the invariant that a governance commitment must never be able to become permanently unreleasable, and delivers Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Single-transaction PoC contract executing the whole `unvote(address _lp)` sequence atomically under the attacker passes an lp address that was never registered in poolInfos, asserting at the end that `userVotedForPoolInVlmgp[user][lp]` still equals `IBribeRewardPool(pool.rewarder).balanceOf(user)` and the PoC's balance delta is non-positive.
