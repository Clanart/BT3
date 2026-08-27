### Title
Unprotected AMM swap due to hardcoded zero slippage in `ArbWomUp3._deposit` mode 2 - ([File: wombat/ArbWomUp3.sol])

### Summary
`ArbWomUp3.incentiveDeposit` lets any wallet deposit WOM and, when `_mode == 2`, routes half of the user's WOM through `SmartWomConvert.convert` with the minimum-received parameter hardcoded to `0`, disabling all slippage protection on the underlying AMM swap. This mirrors the reported `NestedDca` bug class: a slippage-protection value exists conceptually but is never propagated to the function that actually executes the swap, letting an attacker sandwich the transaction and steal value from the depositing user.

### Finding Description
`incentiveDeposit` is a public, unprivileged entry point that transfers the caller's WOM into the contract and calls `_deposit`: [1](#0-0) 

For `_mode == 2`, `_deposit` splits the user's WOM and forwards half to `SmartWomConvert.convert`, passing a **hardcoded `0`** as the `_minRec` argument instead of any user- or caller-supplied slippage bound: [2](#0-1) 

Inside `SmartWomConvert._convertFor`, this `_minRec` of `0` is used twice: first, the actual pool swap is executed with a hardcoded `0` minimum output: [3](#0-2) 

and second, the post-swap sanity check compares the result against the same `_minRec` (`0`), so it can never revert: [4](#0-3) 

There is no parameter on `incentiveDeposit` (or `_deposit`) that lets the caller supply a meaningful minimum-received value for this swap — the value that should protect the trade is never computed or threaded through to the swap call, exactly the class of defect described in the report (a slippage-relevant value that exists in one scope but never reaches the function performing the swap).

### Impact Explanation
Any ordinary wallet calling `incentiveDeposit(..., _mode = 2)` has half of its deposited WOM swapped through the `wom`/`mWom` pool with zero slippage protection. An attacker can sandwich this transaction (front-run to move the pool price, let the victim's swap execute at the worse price, then back-run to capture the difference), directly stealing value from the depositing user's funds. This is a direct theft-of-user-funds impact, not merely a griefing or no-impact issue.

### Likelihood Explanation
The path is trivially reachable by any unprivileged wallet: `incentiveDeposit` is `external`, requires no special role, and `_mode == 2` is a normal, documented code path (the function explicitly branches on `_mode` for "1 stake, 2 lock"). Any MEV searcher monitoring the mempool can detect and sandwich this call, so exploitation requires no privileged access and is economically straightforward whenever meaningful WOM amounts are deposited.

### Recommendation
Add a `_minRec`/slippage parameter to `incentiveDeposit`/`_deposit` for the `_mode == 2` swap path and thread it through to `IConverter(smartWomConvert).convert(toSwap, _convertRatio, _minRec, _mode)` instead of hardcoding `0`. Additionally, review `SmartWomConvert._convertFor`'s internal `swapExactTokensForTokens` call, which also hardcodes `0` as the router-level minimum output, independent of the caller-supplied `_minRec`.

### Proof of Concept
1. Attacker monitors the mempool for calls to `ArbWomUp3.incentiveDeposit` with `_mode == 2`.
2. Attacker front-runs with a large swap on the wom/mWom pool used by `SmartWomConvert`, skewing the price.
3. The victim's transaction executes `_deposit` → `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)` → `_convertFor`, which swaps `buybackAmount` of WOM for mWom via `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` at line 194-196 of `wombat/SmartWomConvert.sol`, receiving a manipulated, unfavorable amount of mWom, and the subsequent check `convertAmount + amountRec < _minRec` (with `_minRec == 0`) never reverts.
4. Attacker back-runs to restore the price and pockets the difference, at the victim's expense.

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

**File:** wombat/SmartWomConvert.sol (L186-197)
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
```

**File:** wombat/SmartWomConvert.sol (L199-206)
```text
        if (convertAmount > 0) {
            IERC20(wom).safeApprove(mWom, convertAmount);
            IMWom(mWom).deposit(convertAmount);
        }

        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();

```
