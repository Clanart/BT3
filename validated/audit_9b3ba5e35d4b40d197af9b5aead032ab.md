### Title
Missing slippage protection in `ArbWomUp3._deposit` exposes users to sandwich attacks when converting WOM to mWom via `SmartWomConvert` - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit()` is callable by any wallet and, for `_mode == 2`, routes half of the deposited WOM through `SmartWomConvert.convert()` with a hardcoded `_minRec` of `0`, removing all slippage protection for the AMM swap leg of the conversion.

### Finding Description
`incentiveDeposit()` is an unprivileged, externally callable entry point that lets a user deposit WOM in exchange for MGP rewards and an mWom position [1](#0-0) . For `_mode == 2` it calls the internal `_deposit` function, which splits the amount and forwards the "swap" half to `SmartWomConvert` with `_minRec` hardcoded to `0`: [2](#0-1) 

Inside `SmartWomConvert._convertFor`, this `_minRec = 0` value is compared against the actual amount received, and it also directly gates the underlying router swap: the `buybackAmount` portion of the WOM is exchanged via `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)`, where `0` is the router's own hardcoded minimum output [3](#0-2) . Because the caller of `incentiveDeposit()` has no way to supply their own slippage tolerance, and the internal call always passes `0`, the resulting mWom amount from the AMM swap is completely unprotected against pool-state changes (e.g., a sandwich attack or unfavorable price movement between submission and execution) — the exact bug class described in the external report ("no protection in place to account for pool/price changes").

### Impact Explanation
A user calling `incentiveDeposit()` with `_mode == 2` can have the swap portion of their WOM sandwiched or executed against unfavorable AMM pricing with zero recourse, since the contract itself enforces a `_minRec` of `0`. This results in direct loss of value (fewer mWom/MGP rewards than expected) for an ordinary user with no way to prevent it, since the parameter is not exposed for user control.

### Likelihood Explanation
Any ordinary wallet calling `incentiveDeposit(..., _mode = 2)` is affected on every call; MEV/sandwich bots can trivially observe pending transactions to this function and front/back-run the swap in `womMWomPool`, making exploitation straightforward and repeatable.

### Recommendation
Expose a user-supplied minimum-received parameter from `incentiveDeposit()` through to `IConverter(smartWomConvert).convert(...)` instead of hardcoding `_minRec = 0`, and/or add a deadline parameter, so users can protect themselves from adverse price movement on the swap leg of the conversion.

### Proof of Concept
1. User calls `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)`.
2. `_deposit` computes `toSwap = amount - amount/2` and calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` [4](#0-3) .
3. `SmartWomConvert._convertFor` swaps `buybackAmount` WOM for mWom via `swapExactTokensForTokens(..., 0, ...)` [5](#0-4) .
4. A bot sandwiches the swap (buys mWom before the tx, sells after), so the user receives far less mWom than the pre-transaction quote implied; the transaction still succeeds because `_minRec = 0` never reverts it.

### Citations

**File:** wombat/ArbWomUp3.sol (L88-105)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _convertRatio, bool _bullMode, uint256 _mode // 1 stake, 2 lock
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;
        
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender, _mode == 2);

        // giving out 50% more bonus
        if (_mode == 2)
            rewardToSend = rewardToSend * 2;

        _deposit(msg.sender, _convertRatio, _amount, _mode);

        IERC20(mgp).safeApprove(address(vlMGP), rewardToSend);
        vlMGP.lockFor(rewardToSend, msg.sender);
        // _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        emit VLMGPRewarded(msg.sender, 0, rewardToSend);
    }
```

**File:** wombat/ArbWomUp3.sol (L189-204)
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

**File:** wombat/SmartWomConvert.sol (L175-205)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountRec = 0;

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
        }

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```
