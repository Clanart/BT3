# Q3172: mWOM.convert - _convert transfers WOM before the veWOM lock is confirmed

## Question
wombat/mWOM.sol: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. With _amount, and the block relative to any pending convertAllWom under attacker control and the veWOM mint returns less than the WOM supplied because of the lockDays curve, can an unprivileged caller sequence `convert(uint256 _amount)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that value must not leave the accounting contract before the step that accounts for it has completed and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `convert(uint256 _amount)` (mechanism: _convert transfers WOM before the veWOM lock is confirmed)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `convert(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, and the block relative to any pending convertAllWom
- Exploit idea: for the _doConvert path, _convert() sends the WOM to wombatStaking and then calls _lockWom, so the WOM is out of this contract's control before the lock result is known and any third party call to convertAllWom in between changes the attribution. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: value must not leave the accounting contract before the step that accounts for it has completed; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `convert(uint256 _amount)` sequence atomically under the veWOM mint returns less than the WOM supplied because of the lockDays curve, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted` and the PoC's balance delta is non-positive.
