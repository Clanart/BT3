# Q5388: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
In wombat/WombatBribeManager.sol, castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Can an unprivileged attacker reach this through `castVotes(bool swapForBnb)` while the attacker passes offsetting positive and negative deltas that net to zero, and drive `poolInfos[lp].totalVoteInVlmgp` out of agreement with `totalVlMgpInVote` - breaking the invariant that a recorded cadence variable must actually gate the operation it appears to pace - for Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `poolInfos[lp].totalVoteInVlmgp` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the attacker passes offsetting positive and negative deltas that net to zero, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that a recorded cadence variable must actually gate the operation it appears to pace.
