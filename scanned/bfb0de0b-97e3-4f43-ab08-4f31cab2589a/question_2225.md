# Q2225: mWOM.convertAndStake - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Note that in wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an attacker holding only tokens bought on market reach it via `convertAndStake(uint256 _amount)` under wombatStaking is holding WOM from an earlier deposit that has not been locked and force `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` apart from `IERC20(mgp).balanceOf(address(this))`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convertAndStake(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convertAndStake(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and the helper routing that stakes the freshly minted mWOM
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `vlMGPAmount = _amount * rewardRatio / DENOMINATOR` must stay reconciled with `IERC20(mgp).balanceOf(address(this))`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Table test over the boundary values of the attacker inputs (_amount and the helper routing that stakes the freshly minted mWOM) under wombatStaking is holding WOM from an earlier deposit that has not been locked, asserting on every row that wrapper supply must never exceed the backing actually secured for it.
