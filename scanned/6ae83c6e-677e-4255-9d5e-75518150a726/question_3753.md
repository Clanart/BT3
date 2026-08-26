# Q3753: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, is there an unprivileged sequence of `deposit(uint256 _amount)` that leaves `IERC20(this).totalSupply()` unreconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violates the invariant that wrapper supply must never exceed the backing actually secured for it, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: helper is set to a SimplePoolHelper and the attacker uses convertAndStake.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under helper is set to a SimplePoolHelper and the attacker uses convertAndStake, asserting at the end that `IERC20(this).totalSupply()` still equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the PoC's balance delta is non-positive.
