# Q3450: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
wombat/WombatBribeManager.sol: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Under the pool the attacker voted for has been deactivated so unvote reverts, is there an unprivileged sequence of `castVotes(bool swapForBnb)` that leaves `earnedRewards reported by claimAllBribes` unreconciled with `the tokens actually transferred by getReward`, violates the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge, and delivers High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the pool the attacker voted for has been deactivated so unvote reverts.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `earnedRewards reported by claimAllBribes` must stay reconciled with `the tokens actually transferred by getReward`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination) under the pool the attacker voted for has been deactivated so unvote reverts, asserting on every row that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge.
