# Q0727: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
rewards/ManualCompound.sol: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Under another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(_rewards[i][j]).balanceOf(address(this))` unreconciled with `the amount this caller actually claimed through multiclaimOnBehalf`, violates the invariant that a fallback settlement branch must be bounded by the caller's own entitlement, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: another user's multiclaimOnBehalf is pending in the mempool and will land in the same block.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange another user's multiclaimOnBehalf is pending in the mempool and will land in the same block, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `IERC20(_rewards[i][j]).balanceOf(address(this))` equals `the amount this caller actually claimed through multiclaimOnBehalf` and that no account can withdraw more than it put in.
