# Q3797: mWomSV.lockFor - lockFor forces a locked mWOM position onto a victim

## Question
wombat/mWomSV.sol: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. With _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3 under attacker control and the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, can an unprivileged caller sequence `lockFor(uint256 _amount, address _for)` so that `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` no longer reconcile, violating the invariant that only the account itself may cause its locked mWOM balance to change and realising High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: lockFor forces a locked mWOM position onto a victim)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: lockFor(uint256,address) is permissionless and is additionally reachable through SmartWomConvert._convertFor mode 2 and ArbWomUp3._deposit mode 2, so a third party can create or enlarge a victim's cooldown-bound position. Precondition: the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder.
- Invariant to test: only the account itself may cause its locked mWOM balance to change; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the victim has a large unsettled userRewards balance in mWOMSVBaseRewarder, call `lockFor(uint256 _amount, address _for)`, and assert `totalAmount` equals `IERC20(mWOM).balanceOf(address(this))` and that no account can withdraw more than it put in.
