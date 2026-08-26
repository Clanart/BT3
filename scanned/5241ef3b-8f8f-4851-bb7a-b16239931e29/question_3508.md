# Q3508: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
In wombat/mWomSV.sol, lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Can an unprivileged attacker reach this through `lockFor(uint256 _amount, address _for)` while the attacker repeats cancelUnlock and startUnlock inside one transaction, and drive `getUserAmountInCoolDown(user)` out of agreement with `totalAmountInCoolDown` - breaking the invariant that only the account itself may cause its locked mWOM balance to change - for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the attacker repeats cancelUnlock and startUnlock inside one transaction.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker repeats cancelUnlock and startUnlock inside one transaction, have the attacker run `lockFor(uint256 _amount, address _for)`, then assert the victim's claimable value and the `getUserAmountInCoolDown(user)` versus `totalAmountInCoolDown` relation are unchanged by the attacker's transaction.
