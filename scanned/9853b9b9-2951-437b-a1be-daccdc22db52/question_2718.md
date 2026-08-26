# Q2718: WombatBribeManager.castVotes - harvestSinglePool drains pending bribes with no caller fee

## Question
In wombat/WombatBribeManager.sol, harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Starting from a state where the attacker votes in the block immediately before a known keeper cast, can an unprivileged EOA use `castVotes(bool swapForBnb)` to leave `delegatedPool votes` inconsistent with `totalVlMgpInVote`, violating the invariant that a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `castVotes(bool swapForBnb)` (mechanism: harvestSinglePool drains pending bribes with no caller fee)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `castVotes(bool swapForBnb)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the exact block at which every pending vote is pushed to Wombat and every bribe is harvested, plus the caller fee destination
- Exploit idea: harvestSinglePool() calls wombatStaking.vote(_lps, zero deltas, rewarders, address(0)), which harvests all pending bribes while passing caller as the zero address so no caller fee is paid, letting an attacker front-run a castVotes and strip the fee that would have compensated it. Precondition: the attacker votes in the block immediately before a known keeper cast.
- Invariant to test: a permissionless harvest must not be usable to strip the incentive from the function that maintains the gauge; concretely, `delegatedPool votes` must stay reconciled with `totalVlMgpInVote`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker votes in the block immediately before a known keeper cast, call `castVotes(bool swapForBnb)`, and assert `delegatedPool votes` equals `totalVlMgpInVote` and that no account can withdraw more than it put in.
