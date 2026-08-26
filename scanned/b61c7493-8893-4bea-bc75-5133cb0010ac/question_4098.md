# Q4098: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `userTotalVotedInVlmgp[msg.sender]` unreconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`, violates the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `userTotalVotedInVlmgp[msg.sender]` must stay reconciled with `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `castVotes(bool swapForBnb)` sequence atomically under the delegated pool holds a significant share of poolInfos[lp].totalVoteInVlmgp, asserting at the end that `userTotalVotedInVlmgp[msg.sender]` still equals `IVLMGP(vlMGP).getUserTotalLocked(msg.sender)` and the PoC's balance delta is non-positive.
