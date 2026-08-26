# Q3408: mWOMSVBaseRewarder.getReward - dust threshold waives the forfeit entirely

## Question
Consider rewards/mWOMSVBaseRewarder.sol, where _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Assuming the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, can an unprivileged attacker turn this into a divergence between `totalStaked()` and `IERC20(mWOMSV).totalSupply()` via `getReward(address _account, address _receiver)`, breaking the invariant that a rounding convenience must not create a settlement size at which the forfeit rule stops applying and producing High - Theft of unclaimed yield?

## Target
- File/function: rewards/mWOMSVBaseRewarder.sol -> `getReward(address _account, address _receiver)` (mechanism: dust threshold waives the forfeit entirely)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `getReward(address _account, address _receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: the settlement timing, reachable through MasterMagpie.multiclaim and through the locker's unlock path
- Exploit idea: _calExpireForfeit() sets forfeitAmount to zero whenever it is below _amount / 1000, so any settlement whose computed forfeit lands under one tenth of a percent is paid out in full. Precondition: the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them.
- Invariant to test: a rounding convenience must not create a settlement size at which the forfeit rule stops applying; concretely, `totalStaked()` must stay reconciled with `IERC20(mWOMSV).totalSupply()`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Single-transaction PoC contract executing the whole `getReward(address _account, address _receiver)` sequence atomically under the attacker holds a dominant share of totalStaked so the forfeit recycles mostly back to them, asserting at the end that `totalStaked()` still equals `IERC20(mWOMSV).totalSupply()` and the PoC's balance delta is non-positive.
