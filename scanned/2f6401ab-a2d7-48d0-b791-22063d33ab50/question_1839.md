# Q1839: ManualCompound.compound - locker branch sends the whole balance to msg.sender

## Question
In rewards/ManualCompound.sol, when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Does `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` let an unprivileged caller exploit that under the caller passes empty inner arrays so the claim-all path runs for every pool, so that `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` diverges from `the caller's own share of that reward token`, the invariant that a locking branch must lock only the caller's own earned amount is broken, and the result is Critical - Direct theft of user funds?

## Target
- File/function: rewards/ManualCompound.sol -> `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)` (mechanism: locker branch sends the whole balance to msg.sender)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: every element of _lps and _rewards, plus _convertRatio, _minRec and _lockMgp, with no restriction on who calls
- Exploit idea: when _locker is configured and _lockMgp is true, ILocker(_locker).lockFor(receivedBalance, msg.sender) locks the contract's entire balance of that token for the caller. Precondition: the caller passes empty inner arrays so the claim-all path runs for every pool.
- Invariant to test: a locking branch must lock only the caller's own earned amount; concretely, `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` must stay reconciled with `the caller's own share of that reward token`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Unit test with mocked Wombat and router legs: arrange the caller passes empty inner arrays so the claim-all path runs for every pool, call `compound(address[] _lps, address[][] _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp)`, and assert `IERC20(rewards[i].tokenAddress).balanceOf(address(this))` equals `the caller's own share of that reward token` and that no account can withdraw more than it put in.
