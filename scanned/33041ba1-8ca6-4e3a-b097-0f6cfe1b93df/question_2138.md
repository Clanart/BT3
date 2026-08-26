# Q2138: ManualCompound.compound - converted output is directed to msg.sender

## Question
rewards/ManualCompound.sol: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Under the configured convertor is SmartWomConvert and _convertRatio is set to zero, is there an unprivileged sequence of `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` that leaves `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` unreconciled with `the caller's own share of that reward token`, violates the invariant that converted value must be attributed to the account that earned it, and delivers Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: the configured convertor is SmartWomConvert and _convertRatio is set to zero.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Table test over the boundary values of the attacker inputs (every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls) under the configured convertor is SmartWomConvert and _convertRatio is set to zero, asserting on every row that converted value must be attributed to the account that earned it.
