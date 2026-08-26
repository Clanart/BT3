# Q0821: BNBZapper.zapInToken - residual balances are not returned to their owner

## Question
Note that in rewards/BNBZapper.sol, zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Can an attacker holding only tokens bought on market reach it via `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` under the caller names a token with a transfer hook they control and force `IERC20(fromToken).balanceOf(address(this))` apart from `amount pulled from msg.sender`, breaking the invariant that value left over from a swap must be returned to the account that supplied it for High - Permanent freezing of unclaimed yield?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: residual balances are not returned to their owner)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: zapInToken() swaps the amount it pulled and returns nothing, so any dust left by a router that consumed less than the approved amount stays on the contract with no owner and no recovery path other than the owner-only withdraw. Precondition: the caller names a token with a transfer hook they control.
- Invariant to test: value left over from a swap must be returned to the account that supplied it; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: High - Permanent freezing of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up the caller names a token with a transfer hook they control, snapshot `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
