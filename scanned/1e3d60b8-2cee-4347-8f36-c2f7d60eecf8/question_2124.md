# Q2124: ArbWomUp3.incentiveDeposit - the caller sets the conversion ratio for the protocol's own routing

## Question
Note that in wombat/ArbWomUp3.sol, _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Can an attacker holding only tokens bought on market reach it via `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` under a residual mWOM balance from an earlier call sits on the contract and force `bracketRewarded` apart from `calDoubledCounted(account)`, breaking the invariant that a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path for Critical - Direct theft of user funds?

## Target
- File/function: wombat/ArbWomUp3.sol -> `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` (mechanism: the caller sets the conversion ratio for the protocol's own routing)
- Entrypoint: unprivileged EOA or attacker-deployed contract calling `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)`; no owner, poolManager, ankrOperator, rewardManager, compounder or ProxyAdmin role
- Attacker controls: _amount, _convertRatio, _bullMode and _mode, with _mode selecting stake, mWomSV lock or plain transfer
- Exploit idea: _deposit() mode 2 forwards the caller's _convertRatio into IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0), so the caller decides how much of the deposit is routed through the AMM. Precondition: a residual mWOM balance from an earlier call sits on the contract.
- Invariant to test: a routing parameter that decides how much value is traded must not be caller-supplied on a shared-balance path; concretely, `bracketRewarded` must stay reconciled with `calDoubledCounted(account)`.
- Expected Immunefi impact: Critical - Direct theft of user funds
- Fast validation: Single-transaction PoC contract executing the whole `incentiveDeposit(uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode)` sequence atomically under a residual mWOM balance from an earlier call sits on the contract, asserting at the end that `bracketRewarded` still equals `calDoubledCounted(account)` and the PoC's balance delta is non-positive.
