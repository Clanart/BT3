# Q2409: mWOM.deposit - _convert transfers WOM before the veWOM lock is confirmed

## Question
In wombat/mWOM.sol, for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Can an unprivileged attacker reach this through `deposit(uint256 _amount)` while wombatStaking is holding WOM from an earlier deposit that has not been locked, and drive `IERC20(wom).balanceOf(address(this))` out of agreement with `totalConverted` - breaking the invariant that value must not leave the accounting contract before the step that accounts for it has completed - for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount)`: constrain the setup so that wombatStaking is holding WOM from an earlier deposit that has not been locked, fuzz the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked), and assert after every call that value must not leave the accounting contract before the step that accounts for it has completed.
