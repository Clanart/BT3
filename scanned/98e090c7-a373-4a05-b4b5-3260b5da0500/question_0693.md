# Q0693: WombatPoolHelper.withdraw - burnReceiptToken is the last step and is not atomic with the payout

## Question
Consider wombat/WombatPoolHelper.sol, where withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Assuming the pool's deposit token is wBNB and the caller arrived through depositNative, can an unprivileged attacker turn this into a divergence between `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` via `withdraw(uint256 _liquidity, uint256 _minAmount)`, breaking the invariant that receipt supply must fall in the same step as the backing it represents and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatPoolHelper.sol -> `withdraw(uint256 _liquidity, uint256 _minAmount)` (mechanism: burnReceiptToken is the last step and is not atomic with the payout)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(uint256 _liquidity, uint256 _minAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, with the payout measured as a balance delta
- Exploit idea: withdraw() pays out, then unstakes, then calls IWombatStaking(wombatStaking).burnReceiptToken, so between the payout and the burn the receipt-token supply still claims backing that has already left. Precondition: the pool's deposit token is wBNB and the caller arrived through depositNative.
- Invariant to test: receipt supply must fall in the same step as the backing it represents; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Invariant/fuzz run over `withdraw(uint256 _liquidity, uint256 _minAmount)`: constrain the setup so that the pool's deposit token is wBNB and the caller arrived through depositNative, fuzz the attacker inputs (_liquidity and _minAmount, with the payout measured as a balance delta), and assert after every call that receipt supply must fall in the same step as the backing it represents.
