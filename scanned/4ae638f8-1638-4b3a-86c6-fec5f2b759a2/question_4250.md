# Q4250: mWOM.incentiveDeposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. With _amount with no cap, and _stake, while rewardRatio is non-zero under attacker control and helper is unset so convertAndStake reverts and only the plain mint path is reachable, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, bool _stake)` so that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that wrapper supply must never exceed the backing actually secured for it and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish helper is unset so convertAndStake reverts and only the plain mint path is reachable, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
