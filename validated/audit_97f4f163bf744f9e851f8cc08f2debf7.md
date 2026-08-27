### Title
Hardcoded zero minimum-amount-out in ArbWomUp3's mode-2 deposit swap enables sandwich attacks on depositors - (File: wombat/ArbWomUp3.sol)

### Summary
`ArbWomUp3.incentiveDeposit` allows any user to deposit WOM with `_mode == 2`, which triggers `_deposit` to swap half the deposited WOM for `mWom` via `SmartWomConvert.convert`. The `_minRec` parameter of that swap call is hardcoded to `0`, exactly mirroring the reported "minimum-amount-out hardcoded to zero" bug class, exposing ordinary depositors to sandwich attacks on the WOM→mWom conversion.

### Finding Description
`ArbWomUp3._deposit` handles the `_mode == 2` branch by splitting the deposit and routing half of it through `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`: [1](#0-0) 

The third argument to `convert` is `_minRec`, which `SmartWomConvert._convertFor` uses as the sole slippage/sandwich protection check before it approves and reverts if not met: [2](#0-1) [3](#0-2) 

Because `ArbWomUp3` always passes `0` for `_minRec` regardless of the amount being swapped or the caller's intent, the depositor has no way to protect themselves against price manipulation of the WOM/mWom pool swap performed via `IWombatRouter(router).swapExactTokensForTokens(...)`. This is functionally identical to the reported issue: a value-bearing swap is executed with a hardcoded zero minimum-amount-out, whereas the underlying facility (`convert`/`convertFor`) actually supports (and is designed to be given) a caller-specified minimum. `ArbWomUp3` throws that protection away for every user who chooses `_mode == 2`.

### Impact Explanation
An MEV bot observing a pending `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` transaction can front-run it by moving the WOM/mWom pool price unfavorably, let the victim's swap execute at the manipulated price (guaranteed to succeed because `_minRec == 0` never reverts), then back-run to restore the price and capture the difference. This is a direct extraction of value from the depositor's own funds during a transaction they intentionally submitted — the sandwich attacker profits at the direct expense of the ordinary user's deposited WOM, with no way for the user to prevent or bound the loss.

### Likelihood Explanation
Likelihood is high for any user who calls `incentiveDeposit` with `_mode == 2`: this is a normal, expected code path (not an edge case), fully reachable by any unprivileged wallet with no special preconditions, and sandwich bots actively monitor mempools for exactly this kind of unprotected swap.

### Recommendation
- Short term: Add a `_minRec` (or equivalent minimum-amount-out) parameter to `incentiveDeposit`/`_deposit` for the `_mode == 2` path and forward the caller-supplied value into `IConverter(smartWomConvert).convert(toSwap, _convertRatio, _minRec, 0)` instead of hardcoding `0`.
- Long term: Audit all internal calls to swap/convert functions across the codebase for hardcoded zero slippage parameters, and document/expose slippage protection consistently wherever user funds pass through an AMM swap.

### Proof of Concept
1. Alice calls `ArbWomUp3.incentiveDeposit(amount, convertRatio, false, 2)` to deposit WOM and lock resulting mWom via mode 2. [4](#0-3) 
2. Internally, `_deposit` computes `toSwap = amount - toDeposit` and calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` — note the hardcoded `0` for `_minRec`. [5](#0-4) 
3. `SmartWomConvert._convertFor` executes `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)`, passing Alice's `_minRec == 0` straight through as the router's minimum-out, and only checks `convertAmount + amountRec < _minRec` (which with `_minRec = 0` never triggers). [3](#0-2) 
4. Eve, observing Alice's pending transaction, front-runs it by swapping WOM for mWom (or vice versa) to skew the pool price, lets Alice's swap execute at the worse price with no revert protection, then back-runs to restore the price and pocket the difference — extracting value directly from Alice's deposit with no recourse.

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

**File:** wombat/SmartWomConvert.sol (L186-206)
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
        }

        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

```
