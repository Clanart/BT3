### Title
Missing slippage protection in `ArbWomUp3::_deposit` (mode 2) due to hardcoded zero `_minRec` passed to `SmartWomConvert::convert` - ([File: wombat/ArbWomUp3.sol])

### Summary
When a user deposits WOM through `ArbWomUp3::incentiveDeposit` with `_mode == 2`, half of the deposited WOM is routed through `SmartWomConvert::convert` with the minimum-output parameter hardcoded to `0`, completely disabling slippage protection on the underlying Wombat pool swap.

### Finding Description
`ArbWomUp3::incentiveDeposit` is a public, unprivileged, non-reentrant function that any wallet can call to deposit WOM and receive locked mWom/MGP rewards: [1](#0-0) 

For `_mode == 2`, it calls the internal `_deposit` function, which splits the deposited amount and routes half of it ("toSwap") into `smartWomConvert.convert(...)` with the `_minRec` argument hardcoded to `0`: [2](#0-1) 

That call reaches `SmartWomConvert::convert` / `_convertFor`, which uses the router to swap WOM for mWom via `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` — note the `0` passed directly as the swap's minimum-output parameter — and then performs a post-swap check `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();` using the same `_minRec` that was forwarded as `0` from `ArbWomUp3`: [3](#0-2) 

Because both the low-level router call's `minimumAmountOut` and the higher-level `_minRec` sanity check are `0`, there is no floor at all on the amount of mWom received for the WOM that gets swapped through the Wombat pool. This matches the reported bug class: the swap has no `minOutAmount`/slippage enforcement, so it can execute at an arbitrarily bad price.

### Impact Explanation
This is High impact: an attacker (or unfavorable price movement while the transaction sits in the mempool) can cause the swapped portion of a user's WOM to be converted into a substantially reduced amount of mWom, e.g., via a sandwich attack against the Wombat `womMWomPool`. Because the value is locked afterward (`mWomSV.lockFor`) as part of a reward program, users directly and permanently lose the difference in value — this is a direct loss of user funds reachable by any ordinary user simply by calling `incentiveDeposit` with `_mode == 2`.

### Likelihood Explanation
Medium: it requires no privileged access — any user calling `incentiveDeposit(_amount, _convertRatio, _bullMode, 2)` is exposed. The likelihood of being exploited depends on mempool visibility and profitability of sandwiching the WOM/mWom pool swap, which is a realistic MEV target on public networks.

### Recommendation
Add a genuine `minOut`/slippage parameter to `ArbWomUp3::_deposit`/`incentiveDeposit` (mode 2) that is supplied by the caller based on an off-chain quote, and forward that non-zero value as `_minRec` to `SmartWomConvert::convert`, instead of hardcoding `0`. This way, both the low-level Wombat router swap and the aggregate `_convertFor` check will actually enforce a minimum acceptable output and revert on excessive slippage.

### Proof of Concept
1. User calls `ArbWomUp3::incentiveDeposit(amount, convertRatio, false, 2)`.
2. Internally, `_deposit` computes `toSwap = amount - amount/2` and calls `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`.
3. `SmartWomConvert::_convertFor` executes `IWombatRouter(router).swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` — with `amountOutMin = 0`.
4. An attacker monitoring the mempool front-runs this transaction with a large swap in the same pool direction, drastically reducing the WOM→mWom exchange rate, then back-runs to restore the price, extracting the difference (sandwich attack). The victim's transaction still succeeds since neither the router call nor the subsequent `_minRec` check (also `0`) can revert it, locking the victim into a materially worse mWom amount than fair value.

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

**File:** wombat/SmartWomConvert.sol (L186-205)
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
