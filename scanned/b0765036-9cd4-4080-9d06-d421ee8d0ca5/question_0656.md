# Q0656: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
wombat/mWomSV.sol - lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Can an unprivileged attacker controlling _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3, under the attacker's slot matured one block ago, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `getUserTotalLocked(user)` and `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked` and the invariant that only the account itself may cause its locked mWOM balance to change, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the attacker's slot matured one block ago.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `getUserTotalLocked(user)` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(mWomSV,user).staked`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `lockFor(uint256 _amount, address _for)`: constrain the setup so that the attacker's slot matured one block ago, fuzz the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3), and assert after every call that only the account itself may cause its locked mWOM balance to change.
