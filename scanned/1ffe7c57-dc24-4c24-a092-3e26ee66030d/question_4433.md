# Q4433: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
Note that in wombat/SmartWomConvert.sol, depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Can an attacker holding only tokens bought on market reach it via `depositFor(uint256 _amount, address _for)` under a residual mWOM balance from an earlier rounding sits in the contract and force `amountRec from swapExactTokensForTokens` apart from `convertAmount minted 1:1 by IMWom(mWom).deposit`, breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: a residual mWOM balance from an earlier rounding sits in the contract.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Single-transaction PoC contract executing the whole `depositFor(uint256 _amount, address _for)` sequence atomically under a residual mWOM balance from an earlier rounding sits in the contract, asserting at the end that `amountRec from swapExactTokensForTokens` still equals `convertAmount minted 1:1 by IMWom(mWom).deposit` and the PoC's balance delta is non-positive.
