# Q3176: AnkrBNBPoolHelper.depositLP - stray receipt tokens on the helper are swept into the next deposit

## Question
wombat/AnkrBNBPoolHelper.sol: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. With _lpAmount under attacker control and a residual stakingToken balance from an earlier rounding sits on the helper, can an unprivileged caller sequence `depositLP(uint256 _lpAmount)` so that `IERC20(stakingToken).balanceOf(address(this)) delta` and `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked` no longer reconcile, violating the invariant that a helper must never credit a depositor with receipt tokens it did not mint for that deposit and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/AnkrBNBPoolHelper.sol -> `depositLP(uint256 _lpAmount)` (mechanism: stray receipt tokens on the helper are swept into the next deposit)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositLP(uint256 _lpAmount)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _lpAmount
- Exploit idea: the balance-delta measurement in _deposit() and depositLP() assumes the helper holds no unattributed stakingToken, so any receipt token left there by a partial mint, a rounding residue or a direct transfer is credited to the next depositor. Precondition: a residual stakingToken balance from an earlier rounding sits on the helper.
- Invariant to test: a helper must never credit a depositor with receipt tokens it did not mint for that deposit; concretely, `IERC20(stakingToken).balanceOf(address(this)) delta` must stay reconciled with `IMasterMagpie(masterMagpie).stakingInfo(stakingToken,_for).staked`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Invariant/fuzz run over `depositLP(uint256 _lpAmount)`: constrain the setup so that a residual stakingToken balance from an earlier rounding sits on the helper, fuzz the attacker inputs (_lpAmount), and assert after every call that a helper must never credit a depositor with receipt tokens it did not mint for that deposit.
