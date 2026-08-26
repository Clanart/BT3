# Q0909: SmartWomConvert.depositFor - depositFor approves MasterMagpie without a reset

## Question
Consider wombat/SmartWomConvert.sol, where depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Assuming the attacker has pushed mWom below buybackThreshold against wom in the same transaction, can an unprivileged attacker turn this into a divergence between `amountRec from swapExactTokensForTokens` and `convertAmount minted 1:1 by IMWom(mWom).deposit` via `depositFor(uint256 _amount, address _for)`, breaking the invariant that a permissionless deposit helper must not be blockable by allowance residue and producing High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/SmartWomConvert.sol -> `depositFor(uint256 _amount, address _for)` (mechanism: depositFor approves MasterMagpie without a reset)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `depositFor(uint256 _amount, address _for)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount and _for, with the mWOM pulled from the caller
- Exploit idea: depositFor() calls IERC20(mWom).safeApprove(masterMagpie, _amount) with no zeroing and is permissionless, so a single under-consuming depositFor bricks the path for everyone. Precondition: the attacker has pushed mWom below buybackThreshold against wom in the same transaction.
- Invariant to test: a permissionless deposit helper must not be blockable by allowance residue; concretely, `amountRec from swapExactTokensForTokens` must stay reconciled with `convertAmount minted 1:1 by IMWom(mWom).deposit`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Invariant/fuzz run over `depositFor(uint256 _amount, address _for)`: constrain the setup so that the attacker has pushed mWom below buybackThreshold against wom in the same transaction, fuzz the attacker inputs (_amount and _for, with the mWOM pulled from the caller), and assert after every call that a permissionless deposit helper must not be blockable by allowance residue.
