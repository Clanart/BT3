# Q5803: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
Note that in wombat/WombatBribeManager.sol, VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an attacker holding only tokens bought on market reach it via `unvote(address _lp)` under the victim has a large unsettled balance in the pool rewarder and force `getVoteForLp(lp) from the Wombat voter` apart from `poolInfos[lp].totalVoteInVlmgp`, breaking the invariant that a governance commitment must never be able to become permanently unreleasable for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `getVoteForLp(lp) from the Wombat voter` must stay reconciled with `poolInfos[lp].totalVoteInVlmgp`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Two-account fork test (victim and attacker): establish the victim has a large unsettled balance in the pool rewarder, have the attacker run `unvote(address _lp)`, then assert the victim's claimable value and the `getVoteForLp(lp) from the Wombat voter` versus `poolInfos[lp].totalVoteInVlmgp` relation are unchanged by the attacker's transaction.
