# Q1063: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
wombat/mWOM.sol: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. With _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked under attacker control and rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, can an unprivileged caller sequence `deposit(uint256 _amount)` so that `rewardRatio` and `DENOMINATOR` no longer reconcile, violating the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up rewardRatio is configured above DENOMINATOR so the bonus exceeds the deposit, snapshot `rewardRatio` and `DENOMINATOR`, run the attacker's `deposit(uint256 _amount)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
