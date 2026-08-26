# Q4836: mWOM.incentiveDeposit - incentiveDeposit costs the attacker nothing because the WOM leg is 1:1

## Question
wombat/mWOM.sol: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Under the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, is there an unprivileged sequence of `incentiveDeposit(uint256 _amount, bool _stake)` that leaves `IERC20(this).totalSupply()` unreconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`, violates the invariant that an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked, and delivers Critical - Direct theft of user funds?

## Target
- File/function: wombat/mWOM.sol -> `incentiveDeposit(uint256 _amount, bool _stake)` (mechanism: incentiveDeposit costs the attacker nothing because the WOM leg is 1:1)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, bool _stake)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount with no cap, and _stake, while rewardRatio is non-zero
- Exploit idea: incentiveDeposit() calls _convert(_amount, _stake, false), which mints mWOM at exactly 1:1 for the WOM supplied, so the caller retains full value on the deposit leg and the vlMGP bonus is pure profit paid from the contract's MGP. Precondition: the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance.
- Invariant to test: an incentive must cost the claimer something the protocol keeps; a 1:1 redeemable leg makes the bonus unbacked; concretely, `IERC20(this).totalSupply()` must stay reconciled with `IERC20(wom).balanceOf(wombatStaking) + veWom backing`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the attacker sizes _amount so that vlMGPAmount exceeds the MGP balance, call `incentiveDeposit(uint256 _amount, bool _stake)`, and assert `IERC20(this).totalSupply()` equals `IERC20(wom).balanceOf(wombatStaking) + veWom backing` and that no account can withdraw more than it put in.
