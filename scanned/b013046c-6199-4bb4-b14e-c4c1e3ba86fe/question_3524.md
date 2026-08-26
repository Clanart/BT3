# Q3524: VLMGP.lockFor - lockFor forces an unwanted position onto a victim

## Question
Note that in VLMGP.sol, lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor and force `getUserTotalLocked(user)` apart from `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`, breaking the invariant that only the account itself may cause its locked balance and its derived governance weight to change for High - Theft of unclaimed yield?

## Target
- File/function: VLMGP.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces an unwanted position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim address) and _amount, including one wei
- Exploit idea: lockFor(uint256,address) is permissionless and calls _lock(msg.sender, _for, _amount), which deposits into MasterMagpie for _for and calls updateTotalFactor(_for), so any third party can mutate a victim's locked balance, their boost factor and their vlMGP pool accrual. Precondition: the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor.
- Invariant to test: only the account itself may cause its locked balance and its derived governance weight to change; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker has never registered a referral code so updateTotalFactor returns before touching totalBoostFactor, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getUserTotalLocked(user)` versus `IMasterMagpie(masterMagpie).stakingInfo(vlMGP,user).staked` relation are unchanged by the attacker's transaction.
