# Q4432: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
Consider wombat/mWOM.sol, where incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Assuming the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, can an unprivileged attacker turn this into a divergence between `IERC20(wom).balanceOf(address(this))` and `totalConverted` via `deposit(uint256 _amount)`, breaking the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked and producing Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `deposit(uint256 _amount)`: constrain the setup so that the attacker sizes _amount so that vlMGPAmount exactly equals the MGP balance, fuzz the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked), and assert after every call that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked.
