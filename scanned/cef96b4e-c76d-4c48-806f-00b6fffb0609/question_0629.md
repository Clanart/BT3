# Q0629: mWOM.incentiveDeposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol - _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker controlling _amount with no cap, and _stake, while rewardRatio is non-zero, under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, exploit this through `incentiveDeposit(uint256 _amount, bool _stake)` to break the reconciliation between `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the invariant that wrapper supply must never exceed the backing actually secured for it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Differential test: perform the same economic action as one call and as several split calls under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, then assert `_amount minted as mWOM` and `mintedVeWomAmount returned by IWombatStaking.convertWOM` end identical in both runs.
