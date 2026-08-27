## Analysis

The reported issue (missing minimum-output/slippage protection allowing a caller to receive less than expected due to state changes at execution time) has a direct analog in `ArbWomUp3.sol`, which sits in the airdrop/incentive distribution scope and is reachable by any ordinary wallet via `incentiveDeposit`.

### Title
Hardcoded Zero Slippage Protection in `ArbWomUp3._deposit` (mode 2) Exposes Users to AMM Sandwich Attacks - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit` with `_mode == 2` swaps half of the user's deposited WOM for mWOM through `SmartWomConvert.convert`, but hardcodes the `_minRec` (minimum-received) parameter to `0`, stripping away the only protection against price movement/front-running on the underlying WOM↔mWOM AMM swap.

### Finding Description
In `_deposit`, mode 2 handling calls: [1](#0-0) 

Specifically the swap call:

```solidity
IERC20(wom).safeApprove(smartWomConvert, toSwap);
IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);
``` [2](#0-1) 

This forwards into `SmartWomConvert._convertFor`, which performs an on-chain AMM swap of the `buybackAmount` portion of WOM for mWOM via `IWombatRouter.swapExactTokensForTokens`, itself called with a hardcoded `amountOutMin = 0`: [3](#0-2) 

The only remaining safety check is `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();` at the end of `_convertFor`: [4](#0-3) 

Because `ArbWomUp3` invokes `convert(toSwap, _convertRatio, 0, 0)` with `_minRec` fixed at `0`, this check can never trigger, and the caller has no way to bound the amount of mWOM they will receive from the swap leg of their deposit. This is functionally identical to the `buyWithReferral` bug class in the external report: the user supplies an input amount and implicitly expects a certain output, but the contract provides no mechanism to guard against the output shrinking due to state changes (here, AMM price movement from a sandwiching transaction) between submission and execution.

Unlike `WombatStaking._sendRewards`, which calls `smartConvert(feeAmount, 0)` — a variant that internally derives its own `_minRec = _amountIn` self-protection inside `smartConvert` — the `convert()` path used by `ArbWomUp3` exposes `_minRec` as a caller-settable parameter, yet `ArbWomUp3` deliberately hardcodes it to zero instead of passing it through as a user input. [5](#0-4) 

### Impact Explanation
An attacker (any unprivileged wallet) can front-run/sandwich a victim's `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` call by trading against the WOM/mWOM pool (`womMWomPool`) referenced by `SmartWomConvert`, temporarily worsening the exchange rate before the victim's swap executes, and then reversing the trade afterward. The victim's `toSwap` portion of WOM will convert to less mWOM than the fair-price amount, and that reduced amount is what gets locked into `mWomSV` on their behalf — a direct, permanent loss of the victim's funds with no possibility of reverting the transaction to avoid it, since `_minRec` is fixed at `0`.

### Likelihood Explanation
Likelihood is Low-Medium: it requires the attacker to detect a pending `incentiveDeposit(_mode=2)` transaction (e.g. via mempool monitoring) and have capital to manipulate the WOM/mWOM pool profitably. The `_convertRatio` parameter is user-supplied, so a sophisticated user could set it to `DENOMINATOR` to bypass the buyback swap entirely and avoid the risk, but this is not enforced or documented, and default/naive usage remains exposed.

### Recommendation
Expose a user-supplied minimum-received parameter in `ArbWomUp3.incentiveDeposit`/`_deposit` and forward it as `_minRec` to `IConverter(smartWomConvert).convert(...)` instead of hardcoding `0`, mirroring the pattern already used correctly in `ManualCompound.compound`, which accepts and forwards a caller-supplied `_minRec`. [6](#0-5) 

### Proof of Concept
1. Victim calls `incentiveDeposit(amount, convertRatio, false, 2)` where `convertRatio < DENOMINATOR`, intending to swap the buyback portion of WOM into mWOM at the current pool rate.
2. Attacker observes the pending transaction in the mempool and front-runs it with a trade on `womMWomPool` that worsens the WOM→mWOM rate.
3. Victim's transaction executes: `_deposit` calls `smartWomConvert.convert(toSwap, convertRatio, 0, 0)`, which internally swaps `buybackAmount` via `router.swapExactTokensForTokens(..., 0, ...)` — accepting whatever output the manipulated pool gives, since the aggregate `_minRec` check also uses `0`.
4. Attacker reverses their trade, pocketing the difference; the victim receives and has locked into `mWomSV` less mWOM than the fair-price amount, with no revert path available to them.

### Citations

**File:** wombat/ArbWomUp3.sol (L189-203)
```text
        } else if (_mode == 2) {
            uint256 toDeposit = _amount / 2;
            uint256 toSwap = _amount - toDeposit;

            // 50% goes to deposit
            IERC20(wom).safeApprove(mWom, toDeposit);
            IMWom(mWom).deposit(toDeposit); 

            // 50% smart smart convert
            IERC20(wom).safeApprove(smartWomConvert, toSwap);
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
```

**File:** wombat/SmartWomConvert.sol (L121-130)
```text
    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
    }
```

**File:** wombat/SmartWomConvert.sol (L186-196)
```text
        if (buybackAmount > 0) {
            address[] memory tokenPath = new address[](2);
            tokenPath[0] = wom;
            tokenPath[1] = mWom;
            address[] memory poolPath = new address[](1);
            poolPath[0] = womMWomPool;
        
            IERC20(wom).safeApprove(router, buybackAmount);
            amountRec = IWombatRouter(router).swapExactTokensForTokens(
                tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp
            );
```

**File:** wombat/SmartWomConvert.sol (L204-205)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```

**File:** rewards/ManualCompound.sol (L119-123)
```text
    // @param _lps lp pool to claim reward from master magpie
    // @param _convertRatio the percentage of total collected wom to convert to mWom with smart convert
    // @param _minRec the expected min mWom to receive upon convert with smart wom convert
    // @param _lockMgp the flag for if MGP should be locked
    function compound(address[] calldata _lps, address[][] calldata _rewards, uint256 _convertRatio, uint256 _minRec, bool _lockMgp) external {
```
