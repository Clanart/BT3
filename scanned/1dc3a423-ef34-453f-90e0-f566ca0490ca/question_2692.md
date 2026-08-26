# Q2692: mWOM.convert - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker reach this through `convert(uint256 _amount)` while the attacker calls convertAllWom on WombatStaking in the same transaction, and drive `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` out of agreement with `IERC20(mgp).balanceOf(address(this))` - breaking the invariant that wrapper supply must never exceed the backing actually secured for it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the attacker calls convertAllWom on WombatStaking in the same transaction, have the attacker run `convert(uint256 _amount)`, then assert the victim's claimable value and the `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` versus `IERC20(mgp).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
