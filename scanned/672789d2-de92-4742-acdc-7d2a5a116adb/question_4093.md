# Q4093: mWOM.deposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
wombat/mWOM.sol: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Under helper is unset so convertAndStake reverts and only the plain mint path is reachable, is there an unprivileged sequence of `deposit(uint256 _amount)` that leaves `rewardRatio` unreconciled with `DENOMINATOR`, violates the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `deposit(uint256 _amount)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `deposit(uint256 _amount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, which mints mWOM 1:1 while leaving the WOM sitting in this contract unlocked
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: helper is unset so convertAndStake reverts and only the plain mint path is reachable.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `rewardRatio` must stay reconciled with `DENOMINATOR`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish helper is unset so convertAndStake reverts and only the plain mint path is reachable, have the attacker run `deposit(uint256 _amount)`, then assert the victim's claimable value and the `rewardRatio` versus `DENOMINATOR` relation are unchanged by the attacker's transaction.
