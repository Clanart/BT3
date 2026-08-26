# Q1816: ManualCompound.compound - converted output is directed to msg.sender

## Question
In rewards/ManualCompound.sol, convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the caller passes empty inner arrays so the claim-all path runs for every pool, so that `IERC20(_rewards[i][j]).balanceOf(address(this))` diverges from `the amount this caller actually claimed through multiclaimOnBehalf`, the invariant that converted value must be attributed to the account that earned it is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: the caller passes empty inner arrays so the claim-all path runs for every pool.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Two-account fork test (victim and attacker): establish the caller passes empty inner arrays so the claim-all path runs for every pool, have the attacker run `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, then assert the victim's claimable value and the `IERC20(_rewards[i][j]).balanceOf(address(this))` versus `the amount this caller actually claimed through multiclaimOnBehalf` relation are unchanged by the attacker's transaction.
