# Q3338: mWomSV.startUnlock - startUnlock has no bribe-manager guard unlike VLMGP

## Question
Note that in wombat/mWomSV.sol, VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Can an attacker holding only tokens bought on market reach it via `startUnlock(uint256 _amountToCoolDown)` under the mWOM balance of the locker is exactly equal to totalAmount before the action and force `totalAmount` apart from `IERC20(mWOM).balanceOf(address(this))`, breaking the invariant that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls for High - Theft of unclaimed yield?

## Target
- File/function: wombat/mWomSV.sol -> `startUnlock(uint256 _amountToCoolDown)` (mechanism: startUnlock has no bribe-manager guard unlike VLMGP)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `startUnlock(uint256 _amountToCoolDown)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountToCoolDown and the timestamps written into the slot
- Exploit idea: VLMGP.startUnlock refuses to drop the locked balance below userTotalVotedInVlmgp, but mWomSV.startUnlock has no equivalent check against any consumer that priced a benefit off getUserTotalLocked. Precondition: the mWOM balance of the locker is exactly equal to totalAmount before the action.
- Invariant to test: any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls; concretely, `totalAmount` must stay reconciled with `IERC20(mWOM).balanceOf(address(this))`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Invariant/fuzz run over `startUnlock(uint256 _amountToCoolDown)`: constrain the setup so that the mWOM balance of the locker is exactly equal to totalAmount before the action, fuzz the attacker inputs (_amountToCoolDown and the timestamps written into the slot), and assert after every call that any external consumer that grants value from getUserTotalLocked must be re-validated when that balance falls.
