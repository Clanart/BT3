# Q3167: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
VLMGP.sol: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. With _for (any victim address) and _amount, including one wei under attacker control and the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, can an unprivileged caller sequence `lockFor(uint256 _amount, address _for)` so that `maxSlot` and `userUnlockings[user].length` no longer reconcile, violating the invariant that only the account itself may cause its locked balance and its derived governance weight to change and realising High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `maxSlot` must stay reconciled with `userUnlockings[user].length`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `lockFor(uint256 _amount, address _for)` sequence atomically under the attacker has registered a referral code so updateTotalFactor is not short-circuited by the myCode == 0 guard, asserting at the end that `maxSlot` still equals `userUnlockings[user].length` and the PoC's balance delta is non-positive.
