# Q3223: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol - _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker controlling _amount and the helper routing that stakes the freshly minted mWOM, under the veWOM mint returns less than the WOM supplied because of the lockDays curve, exploit this through `convertAndStake(uint256 _amount)` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted` and the invariant that wrapper supply must never exceed the backing actually secured for it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish the veWOM mint returns less than the WOM supplied because of the lockDays curve, have the attacker run `convertAndStake(uint256 _amount)`, then assert the victim's claimable value and the `IERC20(wom).balanceOf(address(this))` versus `totalConverted` relation are unchanged by the attacker's transaction.
