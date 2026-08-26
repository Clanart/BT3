# Q0698: WombatBribeManager.castVotes - castVotes writes lastCastTime before the external voter call

## Question
wombat/WombatBribeManager.sol: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `targetVote computed in castVotes` unreconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`, violates the invariant that a cadence marker must only advance once the operation it marks has completed, and delivers Critical - Governance voting result manipulation?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: castVotes writes lastCastTime before the external voter call)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: lastCastTime is assigned before wombatStaking.vote executes, so the recorded cadence advances even for a cast whose downstream effects revert or are re-entered. Precondition: a large bribe has just landed in the Wombat bribe contract and no cast has happened yet.
- Invariant to test: a cadence marker must only advance once the operation it marks has completed; concretely, `targetVote computed in castVotes` must stay reconciled with `totalVotes() from veWom.balanceOf(wombatStaking)`.
- Expected Immunefi impact: Critical - Governance voting result manipulation
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under a large bribe has just landed in the Wombat bribe contract and no cast has happened yet, then assert `targetVote computed in castVotes` and `totalVotes() from veWom.balanceOf(wombatStaking)` end identical in both runs.
