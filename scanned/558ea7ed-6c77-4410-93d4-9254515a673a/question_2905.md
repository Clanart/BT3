# Q2905: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
wombat/mWOM.sol: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Under the attacker calls convertAllWom on WombatStaking in the same transaction, is there an unprivileged sequence of `deposit(uint256 _amount)` that leaves `IERC20(wom).balanceOf(address(this))` unreconciled with `totalConverted`, violates the invariant that wrapper supply must never exceed the backing actually secured for it, and delivers Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker calls convertAllWom on WombatStaking in the same transaction.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker calls convertAllWom on WombatStaking in the same transaction, call `deposit(uint256 _amount)`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted` and that no account can withdraw more than it put in.
