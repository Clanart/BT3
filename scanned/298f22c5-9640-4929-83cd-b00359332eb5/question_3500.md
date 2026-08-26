# Q3500: ManualCompound.compound - converted output is directed to msg.sender

## Question
rewards/ManualCompound.sol: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. With every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls under attacker control and the caller repeats the call in the same block after a large honest claim, can an unprivileged caller sequence `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` so that `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` and `the caller's own share of that reward token` no longer reconcile, violating the invariant that converted value must be attributed to the account that earned it and realising Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: the caller repeats the call in the same block after a large honest claim.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller repeats the call in the same block after a large honest claim, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` equals `the caller's own share of that reward token` and that no account can withdraw more than it put in.
