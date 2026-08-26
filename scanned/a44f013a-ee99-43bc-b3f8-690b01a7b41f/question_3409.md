# Q3409: WombatStaking.withdraw - harvest reverts on a manipulated smart convert and blocks deposits and withdrawals

## Question
In wombat/WombatStaking.sol, SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Can an unprivileged attacker reach this through `withdraw(address,uint256,uint256,address) via a pool helper` while the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, and drive `IERC20(wom).balanceOf(address(this))` out of agreement with `totalConverted in mWOM` - breaking the invariant that a manipulable external price must not be able to block principal deposits and withdrawals - for High - Temporary freezing of funds for at least 24 hours?

## Target
- File/function: wombat/WombatStaking.sol -> `withdraw(address,uint256,uint256,address) via a pool helper` (mechanism: harvest reverts on a manipulated smart convert and blocks deposits and withdrawals)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `withdraw(address,uint256,uint256,address) via a pool helper`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _liquidity and _minAmount, forwarded verbatim from the helper's withdraw
- Exploit idea: SmartWomConvert.smartConvert passes _minRec equal to _amountIn, so a manipulated pool makes it revert, and because _sendRewards runs inside _toMasterWomAndSendReward it is on the path of every deposit, depositLP and withdraw for that pool. Precondition: the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction.
- Invariant to test: a manipulable external price must not be able to block principal deposits and withdrawals; concretely, `IERC20(wom).balanceOf(address(this))` must stay reconciled with `totalConverted in mWOM`.
- Expected Immunefi impact: High - Temporary freezing of funds for at least 24 hours
- Fast validation: Unit test with mocked Wombat and router legs: arrange the wom/mWom Wombat pool has been pushed off peg by the attacker in the same transaction, call `withdraw(address,uint256,uint256,address) via a pool helper`, and assert `IERC20(wom).balanceOf(address(this))` equals `totalConverted in mWOM` and that no account can withdraw more than it put in.
