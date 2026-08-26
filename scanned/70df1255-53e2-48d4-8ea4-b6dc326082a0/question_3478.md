# Q3478: mWOM.incentiveDeposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Does `incentiveDeposit(uint256 _amount, bool _stake)` let an unprivileged caller exploit that under the veWOM mint returns less than the WOM supplied because of the lockDays curve, so that `IERC20(this).totalSupply()` diverges from `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, the invariant that wrapper supply must never exceed the backing actually secured for it is broken, and the result is Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the veWOM mint returns less than the WOM supplied because of the lockDays curve, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
