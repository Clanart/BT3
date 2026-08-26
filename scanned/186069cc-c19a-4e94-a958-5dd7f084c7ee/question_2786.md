# Q2786: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
VLMGP.sol - lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Can an unprivileged attacker controlling _for (any victim address) and _amount, including one wei, under the pool the attacker voted for has since been deactivated so unvote reverts, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)` and the invariant that only the account itself may cause its locked balance and its derived governance weight to change, yielding High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the pool the attacker voted for has since been deactivated so unvote reverts.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `userTotalVotedInVlmgp(user) in WombatBribeManager` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the pool the attacker voted for has since been deactivated so unvote reverts, snapshot `userTotalVotedInVlmgp(user) in WombatBribeManager` and `getUserTotalLocked(user)`, run the attacker's `lockFor(uint256 _amount, address _for)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
