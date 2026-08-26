# Q4407: SmartWomConvert.smartConvert - maxSwapAmount derives from instantaneous cash and liability

## Question
wombat/SmartWomConvert.sol - maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Can an unprivileged attacker controlling _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from, under a residual mWOM balance from an earlier rounding sits in the contract, exploit this through `smartConvert(uint256 _amountIn, uint256 _mode)` to break the reconciliation between `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` and the invariant that a protocol-owned trade ceiling must not be computed from state the caller can set in the same block, yielding High - Theft of unclaimed yield?

## Target
- File/function: wombat/SmartWomConvert.sol -> `smartConvert(uint256 _amountIn, uint256 _mode)` (mechanism: maxSwapAmount derives from instantaneous cash and liability)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `smartConvert(uint256 _amountIn, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amountIn and _mode, plus the pool state that currentRatio and maxSwapAmount are read from
- Exploit idea: maxSwapAmount() returns (womLiability - womCash) * ratio / DENOMINATOR from live IAsset reads, so the ceiling on how much the protocol will swap is set by pool state an attacker can move immediately before calling. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: a protocol-owned trade ceiling must not be computed from state the caller can set in the same block; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Theft of unclaimed yield
- Fast validation: Foundry fork test against the deployed pool: set up a residual mWOM balance from an earlier rounding sits in the contract, snapshot `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit`, run the attacker's `smartConvert(uint256 _amountIn, uint256 _mode)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
