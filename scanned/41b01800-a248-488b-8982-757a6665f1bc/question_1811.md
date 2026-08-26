# Q1811: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and an owner funding transfer of MGP is sitting in the mempool, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` no longer reconcile, violating the invariant that wrapper supply must never exceed the backing actually secured for it and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under an owner funding transfer of MGP is sitting in the mempool, asserting at the end that `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` still equals `IERC20(mgp).balanceOf(address(this))` and the PoC's balance delta is non-positive.
