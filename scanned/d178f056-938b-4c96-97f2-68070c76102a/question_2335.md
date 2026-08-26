# Q2335: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
VLMGP.sol: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. With _for (any victim address) and _amount, including one wei under attacker control and the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, can an unprivileged caller sequence `lockFor(uint256 _amount, address _for)` so that `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` no longer reconcile, violating the invariant that only the account itself may cause its locked balance and its derived governance weight to change and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker has an active vote registered in WombatBribeManager for the amount being unlocked.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `userInfos[user].factor in ReferralStorage` must stay reconciled with `getUserTotalLocked(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker has an active vote registered in WombatBribeManager for the amount being unlocked, then assert `userInfos[user].factor in ReferralStorage` and `getUserTotalLocked(user)` end identical in both runs.
