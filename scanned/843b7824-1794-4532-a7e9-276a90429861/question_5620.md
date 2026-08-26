# Q5620: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
In wombat/WombatBribeManager.sol, castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker passes an lp address that was never registered in poolInfos, and drive `totalVlMgpInVote` out of agreement with `sum of userTotalVotedInVlmgp over all voters` - breaking the invariant that a recorded cadence variable must actually gate the operation it appears to pace - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the attacker passes an lp address that was never registered in poolInfos.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `totalVlMgpInVote` must stay reconciled with `sum of userTotalVotedInVlmgp over all voters`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the attacker passes an lp address that was never registered in poolInfos, asserting at the end that `totalVlMgpInVote` still equals `sum of userTotalVotedInVlmgp over all voters` and the PoC's balance delta is non-positive.
