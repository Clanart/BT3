# Q4869: mWOM.incentiveDeposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol - _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `IERC20(wom).balanceOf(address(this))` and `totalConverted` and the invariant that wrapper supply must never exceed the backing actually secured for it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, then assert `IERC20(wom).balanceOf(address(this))` and `totalConverted` end identical in both runs.
