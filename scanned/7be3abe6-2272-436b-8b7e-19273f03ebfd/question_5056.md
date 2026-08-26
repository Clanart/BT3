# Q5056: mWOM.deposit - mWOM minted on the requested amount rather than on veWOM actually obtained

## Question
Note that in wombat/mWOM.sol, _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount)` under the attacker repeats the call across several addresses in the same block and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted`, breaking the invariant that wrapper supply must never exceed the backing actually secured for it for Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: mWOM minted on the requested amount rather than on veWOM actually obtained)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: _convert() mints exactly _amount of mWOM and then _lockWom() adds only the returned mintedVeWomAmount to totalAccumulated, so a veWOM mint that returns less than expected leaves mWOM supply ahead of real backing. Precondition: the attacker repeats the call across several addresses in the same block.
- Invariant to test: wrapper supply must never exceed the backing actually secured for it; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount)`: constrain the setup so that the attacker repeats the call across several addresses in the same block, fuzz the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked), and assert after every call that wrapper supply must never exceed the backing actually secured for it.
