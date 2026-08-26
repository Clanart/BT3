# Q2386: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `rewardRatio` out of agreement with `DENOMINATOR` - breaking the invariant that wrapper supply must never exceed the backing actually secured for it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked) under wombatStaking is holding WOM from an earlier deposit that has not been locked, asserting on every row that wrapper supply must never exceed the backing actually secured for it.
