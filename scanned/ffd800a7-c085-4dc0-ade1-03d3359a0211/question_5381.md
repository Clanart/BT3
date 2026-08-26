# Q5381: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
Consider wombat/WombatBribeManager.sol, where harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Assuming the attacker passes offsetting positive and negative deltas that net to zero, can an unprivileged attacker turn this into a divergence between `userVotedForPoolInVlmgp[user][lp]` and `IBribeRewardPool(pool.rewarder).balanceOf(user)` via `castVotes(bool swapForBnb)`, breaking the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker passes offsetting positive and negative deltas that net to zero.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `userVotedForPoolInVlmgp[user][lp]` must stay reconciled with `IBribeRewardPool(pool.rewarder).balanceOf(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `castVotes(bool swapForBnb)`: constrain the setup so that the attacker passes offsetting positive and negative deltas that net to zero, fuzz the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination), and assert after every call that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge.
