# Q0603: ManualCompound.compound - caller sets the slippage floor for value that is not theirs

## Question
rewards/ManualCompound.sol: the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` unreconciled with `the caller's own share of that reward token`, violates the invariant that the slippage floor on a shared-balance swap must be derived from protocol state, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: caller sets the slippage floor for value that is not theirs)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: the caller-supplied _minRec is the only slippage protection applied to the convertFor leg, and it is applied to the entire contract balance rather than to the caller's own share. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: the slippage floor on a shared-balance swap must be derived from protocol state; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, asserting on every row that the slippage floor on a shared-balance swap must be derived from protocol state.
