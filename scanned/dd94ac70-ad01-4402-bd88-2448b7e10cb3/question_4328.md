# Q4328: mWOM.convert - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol - _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an unprivileged attacker controlling _amount, and the block relative to any pending convertAllWom, under the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, exploit this through `convert(uint256 _amount)` to break the reconciliation between `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and the invariant that wrapper supply must never exceed the backing actually secured for it, yielding Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, snapshot `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, run the attacker's `convert(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
