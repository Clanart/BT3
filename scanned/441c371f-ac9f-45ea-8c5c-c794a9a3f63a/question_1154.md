# Q1154: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that wrapper supply must never exceed the backing actually secured for it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting at the end that `_amount minted as mWOM` still equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the PoC's balance delta is non-positive.
