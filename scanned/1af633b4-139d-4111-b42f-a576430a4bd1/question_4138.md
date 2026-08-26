# Q4138: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while helper is unset so convertAndStake reverts and only the plain mint path is reachable, and drive `_amount minted as mWOM` out of agreement with `mintedVeWomAmount returned by IWombatStaking.convertWOM` - breaking the invariant that wrapper supply must never exceed the backing actually secured for it - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish helper is unset so convertAndStake reverts and only the plain mint path is reachable, have the attacker run `deposit(uint256 _amount)`, then assert the victim's claimable value and the `_amount minted as mWOM` versus `mintedVeWomAmount returned by IWombatStaking.convertWOM` relation are unchanged by the attacker's transaction.
