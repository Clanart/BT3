# Q2652: mWOM.incentiveDeposit - first caller after funding takes the whole incentive

## Question
wombat/mWOM.sol: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. With _amount with no cap, and _stake, while rewardRatio is non-zero under attacker control and wombatStaking is holding WOM from an earlier deposit that has not been locked, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, bool _stake)` so that `IERC20(wom).balanceOf(address(this))` and `totalConverted` no longer reconcile, violating the invariant that a shared incentive pot must not be fully claimable by a single actor in one transaction and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: first caller after funding takes the whole incentive)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: because the MGP incentive balance is a shared pot and incentiveDeposit applies no queue, cap or per-block limit, the first address to observe a funding transaction can take the entire top-up in the same block. Precondition: wombatStaking is holding WOM from an earlier deposit that has not been locked.
- Invariant to test: a shared incentive pot must not be fully claimable by a single actor in one transaction; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish wombatStaking is holding WOM from an earlier deposit that has not been locked, have the attacker run `incentiveDeposit(uint256 _amount, bool _stake)`, then assert the victim's claimable value and the `IERC20(wom).balanceOf(address(this))` versus `totalConverted` relation are unchanged by the attacker's transaction.
