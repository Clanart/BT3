# Q0170: BNBZapper.zapInToken - no per-caller accounting at all

## Question
rewards/BNBZapper.sol: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Under the router leaves a non-zero allowance after the swap, is there an unprivileged sequence of `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` that leaves `IERC20(fromToken).balanceOf(address(this))` unreconciled with `amount pulled from msg.sender`, violates the invariant that a contract that holds user value even transiently must attribute it per account, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: no per-caller accounting at all)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Precondition: the router leaves a non-zero allowance after the swap.
- Invariant to test: a contract that holds user value even transiently must attribute it per account; concretely, `IERC20(fromToken).balanceOf(address(this))` must stay reconciled with `amount pulled from msg.sender`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the router leaves a non-zero allowance after the swap, snapshot `IERC20(fromToken).balanceOf(address(this))` and `amount pulled from msg.sender`, run the attacker's `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
