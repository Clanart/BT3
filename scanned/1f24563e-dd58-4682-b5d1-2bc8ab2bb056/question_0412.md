# Q0412: mWOM.deposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Starting from a state where rewardRatio has been switched on and the contract holds a freshly funded MGP balance, can an unprivileged EOA use `deposit(uint256 _amount)` to leave `_amount minted as mWOM` inconsistent with `mintedVeWomAmount returned by IWombatStaking.convertWOM`, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and extracting Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: rewardRatio has been switched on and the contract holds a freshly funded MGP balance.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under rewardRatio has been switched on and the contract holds a freshly funded MGP balance, asserting at the end that `_amount minted as mWOM` still equals `mintedVeWomAmount returned by IWombatStaking.convertWOM` and the PoC's balance delta is non-positive.
