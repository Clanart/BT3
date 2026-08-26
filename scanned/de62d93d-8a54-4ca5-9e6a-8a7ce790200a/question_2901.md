# Q2901: mWomSV.lockFor - matured slot decays the rewardable percent toward zero

## Question
wombat/mWomSV.sol - for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Can an unprivileged attacker controlling _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3, under the attacker holds a second address so lockFor can be used across two accounts, exploit this through `lockFor(uint256 _amount, address _for)` to break the reconciliation between `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` and the invariant that a user must not lose vested value merely because they redeemed late, yielding High - Permanent freezing of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `lockFor(uint256 _amount, address _for)` (mechanism: matured slot decays the rewardable percent toward zero)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `lockFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _for (any victim) and _amount, reachable directly and through SmartWomConvert mode 2 and ArbWomUp3
- Exploit idea: for a slot past endTime the percent contribution is amountInCoolDown * 1e18 * (endTime - startTime) / userTotalVlmgp / (timeNow - startTime), which decays without a floor, so delay alone confiscates a user's whole vesting entitlement. Precondition: the attacker holds a second address so lockFor can be used across two accounts.
- Invariant to test: a user must not lose vested value merely because they redeemed late; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker holds a second address so lockFor can be used across two accounts, call `lockFor(uint256 _amount, address _for)`, and assert `totalAmount` equals `IERC20(mWOM).balanceOf(address(this))` and that no account can withdraw more than it put in.
