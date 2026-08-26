# Q1687: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
In wombat/mWomSV.sol, lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Starting from a state where the attacker arrived through SmartWomConvert.convertFor with _mode == 2, can an unprivileged EOA use `lockFor(uint256 _amount, address _for)` to leave `totalAmount` inconsistent with `IERC20(mWOM).balanceOf(address(this))`, violating the invariant that only the account itself may cause its locked mWOM balance to change and extracting High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the attacker arrived through SmartWomConvert.convertFor with _mode == 2.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Table test over the boundary values of the attacker inputs (_for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3) under the attacker arrived through SmartWomConvert.convertFor with _mode == 2, asserting on every row that only the account itself may cause its locked mWOM balance to change.
