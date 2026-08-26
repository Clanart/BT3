# Q4693: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Consider wombat/mWOM.sol, where _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Assuming the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, can an unprivileged attacker turn this into a divergence between `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` and `IERC20(mgp).balanceOf(address(this))` via `convertAndStake(uint256 _amount)`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `convertAndStake(uint256 _amount)`: constrain the setup so that the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, fuzz the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM), and assert after every call that wrapper supply must never exceed the backing actually secured for it.
