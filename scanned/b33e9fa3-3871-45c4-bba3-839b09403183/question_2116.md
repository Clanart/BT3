# Q2116: WombatBribeManager.claimBribeFor - claimBribeFor settles any victim at an attacker-chosen instant

## Question
Consider wombat/WombatBribeManager.sol, where claimBribeFor(address[],address) has no access control and calls IBribeRewardPool(rewarder).getReward(_for, _for), so a third party fixes when a victim's bribe accrual is settled and their reward index advanced. Assuming the attacker locks vlMGP, votes and casts inside a single transaction, can an unprivileged attacker turn this into a divergence between `poolInfos[lp].isActive` and `userVotedForPoolInVlmgp[user][lp]` via `claimBribeFor(address[] lps, address _for)`, breaking the invariant that only the account itself may decide when its bribe accrual is settled and producing High - Theft of unclaimed yield?

## Target
- File/function: wombat/WombatBribeManager.sol -> `claimBribeFor(address[] lps, address _for)` (mechanism: claimBribeFor settles any victim at an attacker-chosen instant)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `claimBribeFor(address[] lps, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and the lp array
- Exploit idea: claimBribeFor(address[],address) has no access control and calls IBribeRewardPool(rewarder).getReward(_for, _for), so a third party fixes when a victim's bribe accrual is settled and their reward index advanced. Precondition: the attacker locks vlMGP, votes and casts inside a single transaction.
- Invariant to test: only the account itself may decide when its bribe accrual is settled; concretely, `poolInfos[lp].isActive` must stay reconciled with `userVotedForPoolInVlmgp[user][lp]`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker locks vlMGP, votes and casts inside a single transaction, have the attacker run `claimBribeFor(address[] lps, address _for)`, then assert the victim's claimable value and the `poolInfos[lp].isActive` versus `userVotedForPoolInVlmgp[user][lp]` relation are unchanged by the attacker's transaction.
