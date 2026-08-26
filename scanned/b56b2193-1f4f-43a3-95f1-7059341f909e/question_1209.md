# Q1209: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
In wombat/mWomSV.sol, lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Does `lockFor(uint256 _amount, address _for)` let an unprivileged caller exploit that under the attacker reached maxSlot so slot reuse is forced, so that `getUserAmountInCoolDown(user)` diverges from `totalAmountInCoolDown`, the invariant that only the account itself may cause its locked mWOM balance to change is broken, and the result is High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the attacker reached maxSlot so slot reuse is forced.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `getUserAmountInCoolDown(user)` must stay reconciled with `totalAmountInCoolDown`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker reached maxSlot so slot reuse is forced, then assert `getUserAmountInCoolDown(user)` and `totalAmountInCoolDown` end identical in both runs.
