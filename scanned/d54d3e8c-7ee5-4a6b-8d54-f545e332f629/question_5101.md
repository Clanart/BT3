# Q5101: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
wombat/WombatBribeManager.sol: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the attacker passes the same lp address several times in one array, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `userTotalVotedInVlmgp[msg.sender]` and `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` no longer reconcile, violating the invariant that a recorded cadence variable must actually gate the operation it appears to pace and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the attacker passes the same lp address several times in one array.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker passes the same lp address several times in one array, call `castVotes(bool swapForBnb)`, and assert `userTotalVotedInVlmgp[msg.sender]` equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and that no account can withdraw more than it put in.
