# Q5325: WombatBribeManager.unvote - a stuck vote permanently blocks the vlMGP exit

## Question
In wombat/WombatBribeManager.sol, VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Can an unprivileged attacker reach this through `unvote(address _lp)` while the attacker passes offsetting positive and negative deltas that net to zero, and drive `totalVlMgpInVote` out of agreement with `sum of userTotalVotedInVlmgp over all voters` - breaking the invariant that a governance commitment must never be able to become permanently unreleasable - for Critical - Permanent freezing of funds?

## Target
- File/function: wombat/WombatBribeManager.sol -> `unvote(address _lp)` (mechanism: a stuck vote permanently blocks the vlMGP exit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `unvote(address _lp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lp and the moment the whole position on that pool is released
- Exploit idea: VLMGP.startUnlock() reverts when the remaining locked balance would fall below userTotalVotedInVlmgp(msg.sender), and unvote() is the only way to reduce that figure, so a vote that can no longer be withdrawn locks the underlying MGP forever. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a governance commitment must never be able to become permanently unreleasable; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Permanent freezing of funds
- Fast validation: Foundry fork test against the deployed pool: set up the attacker passes offsetting positive and negative deltas that net to zero, snapshot `totalVlMgpInVote` and `sum of userTotalVotedInVlmgp over all voters`, run the attacker's `unvote(address _lp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
