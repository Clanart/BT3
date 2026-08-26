# Q2885: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
wombat/WombatStaking.sol: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. With _liquidity and _minAmount, forwarded verbatim from the helper's withdraw under attacker control and smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, can an unprivileged caller sequence `withdraw(address,uint256,uint256,address) via a pool helper` so that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` and `lpReceived credited by IMintableERC20(receiptToken).mint` no longer reconcile, violating the invariant that receipt-token supply must fall in the same transaction as the backing it represents and realising Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `IERC20(poolInfo.lpAddress).balanceOf(address(this))` must stay reconciled with `lpReceived credited by IMintableERC20(receiptToken).mint`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish smartWomConverter is unset so the fee leg falls back to IMWom(mWom).deposit, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `IERC20(poolInfo.lpAddress).balanceOf(address(this))` versus `lpReceived credited by IMintableERC20(receiptToken).mint` relation are unchanged by the attacker's transaction.
