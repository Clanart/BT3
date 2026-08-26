# Q0098: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
Note that in wombat/mWomSV.sol, lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Can an attacker holding only tokens bought on market reach it via `lockFor(uint256 _amount, address _for)` under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18 and force `mWomSV.getUserTotalLocked(user)` apart from `ArbWomUp3.calDoubledCounted(user)`, breaking the invariant that only the account itself may cause its locked mWOM balance to change for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `mWomSV.getUserTotalLocked(user)` must stay reconciled with `ArbWomUp3.calDoubledCounted(user)`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker is inside the cooldown window so getRewardablePercentWAD is exactly 1e18, then assert `mWomSV.getUserTotalLocked(user)` and `ArbWomUp3.calDoubledCounted(user)` end identical in both runs.
