# Q2520: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
wombat/mWomSV.sol - lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Can an unprivileged attacker controlling _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3, under a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `userUnlockings[user][i].amountInCoolDown` and `maxSlot` and the invariant that only the account itself may cause its locked mWOM balance to change, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `userUnlockings[user][i].amountInCoolDown` must stay reconciled with `maxSlot`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange a large mWOM bonus distribution has just been queued into mWOMSVBaseRewarder, call `lockFor(uint256 _amount, address _for)`, and assert `userUnlockings[user][i].amountInCoolDown` equals `maxSlot` and that no account can withdraw more than it put in.
