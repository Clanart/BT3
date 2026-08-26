# Q4113: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
In wombat/WombatBribeManager.sol, castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Does `castVotes(bool swapForBnb)` let an unprivileged caller exploit that under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, so that `delegatedPool votes` diverges from `totalVlMgpInVote`, the invariant that a recorded cadence variable must actually gate the operation it appears to pace is broken, and the result is Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Two-account fork test (victim and attacker): establish the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, have the attacker run `castVotes(bool swapForBnb)`, then assert the victim's claimable value and the `delegatedPool votes` versus `totalVlMgpInVote` relation are unchanged by the attacker's transaction.
