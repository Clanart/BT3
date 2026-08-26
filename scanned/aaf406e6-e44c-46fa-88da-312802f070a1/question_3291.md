# Q3291: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
In wombat/mWOM.sol, incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Does `deposit(uint256 _amount)` let an unprivileged caller exploit that under the veWOM mint returns less than the WOM supplied because of the lockDays curve, so that `_amount minted as mWOM` diverges from `mintedVeWomAmount returned by IWombatStaking.convertWOM`, the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: the veWOM mint returns less than the WOM supplied because of the lockDays curve.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `_amount minted as mWOM` must stay reconciled with `mintedVeWomAmount returned by IWombatStaking.convertWOM`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (_amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked) under the veWOM mint returns less than the WOM supplied because of the lockDays curve, asserting on every row that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked.
