# Q1111: ArbWomUp2.incentiveDeposit - the reward is computed before the deposit is recorded

## Question
wombat/ArbWomUp2.sol: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. With _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens under attacker control and the caller splits the deposit across several addresses, can an unprivileged caller sequence `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` so that `_minMGPRec supplied by the caller` and `the MGP actually received by the swap` no longer reconcile, violating the invariant that the tier input and the deposit record must be derived from one snapshot and realising Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp2.sol -> `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` (mechanism: the reward is computed before the deposit is recorded)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _minMGPRec and _bullMode, all caller-supplied, with the bull leg swapping the contract's own tokens
- Exploit idea: incentiveDeposit() reads this.getRewardAmount(_amount, msg.sender) before _deposit(_amount) updates userWOMDeposited, so the tier input and the deposit record are taken from different views. Precondition: the caller splits the deposit across several addresses.
- Invariant to test: the tier input and the deposit record must be derived from one snapshot; concretely, `_minMGPRec supplied by the caller` must stay reconciled with `the MGP actually received by the swap`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _minMGPRec, bool _bullMode)` sequence atomically under the caller splits the deposit across several addresses, asserting at the end that `_minMGPRec supplied by the caller` still equals `the MGP actually received by the swap` and the PoC's balance delta is non-positive.
