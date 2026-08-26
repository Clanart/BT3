# Q0381: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
In wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `deposit(uint256 _amount)` to leave `IERC20(this).totalSupply()` inconsistent with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violating the invariant that wrapper supply must never exceed the backing actually secured for it and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Foundry fork test against the deployed pool: set up rewardRatio has been switched on and the contract holds a freshly funded MGP balance, snapshot `IERC20(this).totalSupply()` and `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
