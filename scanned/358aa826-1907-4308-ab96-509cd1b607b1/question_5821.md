# Q5821: WombatBribeManager.castVotes - lastCastTime is written but never enforced

## Question
wombat/WombatBribeManager.sol: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. With the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination under attacker control and the victim has a large unsettled balance in the pool rewarder, can an unprivileged caller sequence `castVotes(bool swapForBnb)` so that `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` no longer reconcile, violating the invariant that a recorded cadence variable must actually gate the operation it appears to pace and realising Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: lastCastTime is written but never enforced)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: castVotes() sets lastCastTime = block.timestamp at the top and nothing anywhere reads it as a rate limit, so there is no minimum interval between casts and no protection against repeated casts inside one block. Precondition: the victim has a large unsettled balance in the pool rewarder.
- Invariant to test: a recorded cadence variable must actually gate the operation it appears to pace; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the victim has a large unsettled balance in the pool rewarder, asserting at the end that `targetVote computed in castVotes` still equals `totalVotes() from veWom.balanceOf(wombatStaking)` and the PoC's balance delta is non-positive.
