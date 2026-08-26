# Q5741: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
Consider wombat/WombatBribeManager.sol, where lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Assuming the bribe contract for the pool registers more than one reward token, can an unprivileged attacker turn this into a divergence between `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` via `castVotes(bool swapForBnb)`, breaking the invariant that a cadence marker must only advance once the operation it marks has completed and producing Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: the bribe contract for the pool registers more than one reward token.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Unit test with mocked Wombat and router legs: arrange the bribe contract for the pool registers more than one reward token, call `castVotes(bool swapForBnb)`, and assert `targetVote computed in castVotes` equals `totalVotes() from veWom.balanceOf(wombatStaking)` and that no account can withdraw more than it put in.
