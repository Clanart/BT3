# Q0293: ManualCompound.compound - fallback branch transfers the whole balance to msg.sender

## Question
In rewards/ManualCompound.sol, when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under a previous compound left a residue of one of the configured reward tokens on the contract, so that `_minRec supplied by the caller` diverges from `obtainedmWomAmount in SmartWomConvert`, the invariant that a fallback settlement branch must be bounded by the caller's own entitlement is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: fallback branch transfers the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when no convertor, locker or helper is configured the branch falls through to IERC20(_tokenAddress).safeTransfer(msg.sender, receivedBalance), moving the entire balance out. Precondition: a previous compound left a residue of one of the configured reward tokens on the contract.
- Invariant to test: a fallback settlement branch must be bounded by the caller's own entitlement; concretely, `_minRec supplied by the caller` must stay reconciled with `obtainedmWomAmount in SmartWomConvert`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up a previous compound left a residue of one of the configured reward tokens on the contract, snapshot `_minRec supplied by the caller` and `obtainedmWomAmount in SmartWomConvert`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
