# Q0728: BNBZapper.zapInToken - no per-caller accounting at all

## Question
In rewards/BNBZapper.sol, the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Does `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` let an unprivileged caller exploit that under the caller sets minRec to zero and sandwiches the PancakeSwap pair, so that `minRec supplied by the caller` diverges from `amounts[amounts.length - 1] returned by the router`, the invariant that a contract that holds user value even transiently must attribute it per account is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/BNBZapper.sol -> `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)` (mechanism: no per-caller accounting at all)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: fromToken, amount, minRec and receiver, all unrestricted
- Exploit idea: the contract holds no ledger of who supplied what, so any token balance on it is indistinguishable from any other and the next call operates against a shared pot. Precondition: the caller sets minRec to zero and sandwiches the PancakeSwap pair.
- Invariant to test: a contract that holds user value even transiently must attribute it per account; concretely, `minRec supplied by the caller` must stay reconciled with `amounts[amounts.length - 1] returned by the router`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller sets minRec to zero and sandwiches the PancakeSwap pair, call `zapInToken(address fromToken, uint256 amount, uint256 minRec, address receiver)`, and assert `minRec supplied by the caller` equals `amounts[amounts.length - 1] returned by the router` and that no account can withdraw more than it put in.
