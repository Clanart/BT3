# Q3262: ManualCompound.compound - converted output is directed to msg.sender

## Question
In rewards/ManualCompound.sol, convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Starting from a state where the reward token has a transfer hook the caller controls, can an unprivileged EOA use `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` to leave `IERC20(_rewards[i][j]).balanceOf(address(this))` inconsistent with `the amount this caller actually claimed through multiclaimOnBehalf`, violating the invariant that converted value must be attributed to the account that earned it and extracting Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: converted output is directed to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: convertFor is called with _for set to msg.sender and _mode set to 2, so the whole converted balance is locked into mWomSV for the caller regardless of whose reward it originally was. Precondition: the reward token has a transfer hook the caller controls.
- Invariant to test: converted value must be attributed to the account that earned it; concretely, `IERC20(_rewards[i][j]).balanceOf(address(this))` must stay reconciled with `the amount this caller actually claimed through multiclaimOnBehalf`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Foundry fork test against the deployed pool: set up the reward token has a transfer hook the caller controls, snapshot `IERC20(_rewards[i][j]).balanceOf(address(this))` and `the amount this caller actually claimed through multiclaimOnBehalf`, run the attacker's `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` sequence, then assert the two still reconcile and the attacker's net token balance did not increase.
