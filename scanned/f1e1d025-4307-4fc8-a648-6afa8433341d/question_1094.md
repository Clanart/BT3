# Q1094: mWOM.deposit - rewardRatio is explicitly allowed to exceed one hundred percent

## Question
wombat/mWOM.sol: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: rewardRatio is explicitly allowed to exceed one hundred percent)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: the contract comment states the ratio can be more than 100%, and setRewardRatio applies no DENOMINATOR ceiling, so the vlMGP paid can exceed the value of the WOM deposited and the incentive pot drains faster than it is funded. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: the value paid out per unit deposited must be bounded so the incentive cannot exceed its funding; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Single-transaction PoC contract executing the whole `deposit(uint256 _amount)` sequence atomically under rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, asserting at the end that `IERC20(wom).balanceOf(address(this))` still equals `totalConverted` and the PoC's balance delta is non-positive.
