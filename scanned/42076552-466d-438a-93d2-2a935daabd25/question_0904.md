# Q0904: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
wombat/mWomSV.sol - VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an unprivileged attacker controlling _amountToCoolDown and the timestamps written into the slot, under the attacker's slot matured one block ago, exploit this through `startUnlock(uint256 _amountToCoolDown)` to break the reconciliation between `totalAmount` and `IERC20(mWOM).balanceOf(address(this))` and the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the attacker's slot matured one block ago.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Two-account fork test (victim and attacker): establish the attacker's slot matured one block ago, have the attacker run `startUnlock(uint256 _amountToCoolDown)`, then assert the victim's claimable value and the `totalAmount` versus `IERC20(mWOM).balanceOf(address(this))` relation are unchanged by the attacker's transaction.
