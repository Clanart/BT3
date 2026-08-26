# Q1740: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
Note that in wombat/mWOM.sol, incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Can an attacker holding only tokens bought on market reach it via `deposit(uint256 _amount)` under an owner funding transfer of MGP is sitting in the mempool and force `IERC20(wom).balanceOf(address(this))` apart from `totalConverted`, breaking the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked for Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: an owner funding transfer of MGP is sitting in the mempool.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked) under an owner funding transfer of MGP is sitting in the mempool, asserting on every row that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked.
