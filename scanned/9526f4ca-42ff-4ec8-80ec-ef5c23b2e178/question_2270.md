# Q2270: WombatStaking.withdraw - burnReceiptToken is decoupled from the value already paid out

## Question
Consider wombat/WombatStaking.sol, where withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Assuming a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, can an unprivileged attacker turn this into a divergence between `isPoolFeeFree[_lpToken]` and `feeInfos.length` via `withdraw(address,uint256,uint256,address) via a pool helper`, breaking the invariant that receipt-token supply must fall in the same transaction as the backing it represents and producing Critical - Protocol insolvency?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: burnReceiptToken is decoupled from the value already paid out)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: withdraw() releases the underlying and burnReceiptToken() is a separate external call the helper must remember to make, so any helper path that pays out without burning leaves receipt tokens outstanding against removed backing. Precondition: a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert.
- Invariant to test: receipt-token supply must fall in the same transaction as the backing it represents; concretely, `isPoolFeeFree[_lpToken]` must stay reconciled with `feeInfos.length`.
- Expected Immunefi impact: Critical - Protocol insolvency
- Fast validation: Two-account fork test (victim and attacker): establish a fee entry with isMWOM set is active and smartWomConverter is a live SmartWomConvert, have the attacker run `withdraw(address,uint256,uint256,address) via a pool helper`, then assert the victim's claimable value and the `isPoolFeeFree[_lpToken]` versus `feeInfos.length` relation are unchanged by the attacker's transaction.
