# Q1794: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Under the attacker locks vlMGP, votes and casts inside a single transaction, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `poolInfos[lp].isActive` unreconciled with `userVotedForPoolInVlmgp[user][lp]`, violates the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the attacker locks vlMGP, votes and casts inside a single transaction, snapshot `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]`, run the attacker's `castVotes(bool swapForBnb)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
