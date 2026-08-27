Confirmed: this is a reachable, unprivileged path. `WombatStaking._sendRewards` is triggered on every ordinary harvest/deposit/withdraw call (via `_toMasterWomAndSendReward` → `_sendRewards`), and when a WOM fee is configured as `isMWOM`, it calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` on behalf of the protocol, no user-controlled slippage input. [1](#0-0) 

Inside `smartConvert`, the "buyback" portion is swapped through the live Wombat AMM pool with **zero minimum output enforced at the swap call itself** (`swapExactTokensForTokens(..., 0, ...)`), relying only on a spot-price read (`currentRatio`) taken moments earlier in the same transaction to decide how much to swap: [2](#0-1) [3](#0-2) 

### Title
Zero-slippage spot-price swap in `smartConvert`/`_convertFor` enables sandwich theft of protocol WOM fees - ([File: wombat/SmartWomConvert.sol])

### Summary
`SmartWomConvert._convertFor` executes the buyback leg through `IWombatRouter.swapExactTokensForTokens` with `amountOutMin` hardcoded to `0`. The decision of how much WOM to route through the swap versus directly convert 1:1 is based on `currentRatio()`, a single spot-price read from the same WOM/mWOM Wombat pool that is about to be swapped against, taken in the same call.

### Finding Description
Any unprivileged wallet can call `WombatStaking.harvest(_lpToken)` (public, only requires an active pool) at any time. This routes newly harvested WOM fees through `_sendRewards`, which — when the `isMWOM` fee is configured — calls `smartConvert(feeAmount, 0)` on `SmartWomConvert`. `smartConvert` reads `currentRatio()` off the live Wombat `womMWomPool`, decides `amountToSwap`, then calls `_convertFor`, which performs the swap with `swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, address(this), block.timestamp)` [4](#0-3) . Because the swap's own `amountOutMin` is `0`, the only protection is the final `convertAmount + amountRec < _minRec` check, and for `smartConvert` that `_minRec` is fixed to `_amountIn` [5](#0-4) . This still permits significant slippage because `mWomToWom` (the buyback trigger threshold) and the swap execution price are read/executed at essentially the same spot price that an attacker can move beforehand via a large WOM→mWOM or mWOM→WOM swap on the same Wombat pool (no flash loan even required if the attacker has capital, and BSC/Wombat pools support large single-block swaps). An attacker can:
1. Push the WOM/mWOM pool price to make `currentRatio() < buybackThreshold`, forcing a buyback swap to occur.
2. Trigger (or wait for) any ordinary user's `harvest`/`deposit` call that causes `_sendRewards` to invoke `smartConvert` with protocol-owned WOM fees.
3. Immediately reverse the price move, capturing the spread extracted from the protocol's buyback swap, since the swap executes at the manipulated price with no independent minimum-output guard.

This directly mirrors the SVT bug class: a component performs value calculation/exchange purely from a manipulable on-pool spot price within one transaction window, with no protection at the point of actual token exchange.

### Impact Explanation
The WOM fees being converted are unclaimed protocol/reward-pool yield (destined for `IBaseRewardPool.queueNewRewards`), owned collectively by stakers/lockers. Sandwiching the zero-slippage buyback swap directly siphons value out of this yield pool into the attacker's pocket — a theft of unclaimed yield belonging to Magpie users, and each occurrence permanently reduces the amount ultimately distributed as rewards (no rollback possible once swapped).

### Likelihood Explanation
`WombatStaking.harvest` is a public, permissionless function that can be called by anyone at will, and every LP pool harvest can trigger this fee-conversion path once `isMWOM` fees and `smartWomConverter` are configured (a realistic production configuration given the contract explicitly supports it). An attacker only needs enough capital (or repeated smaller trades) to move the WOM/mWOM pool price and can trigger `harvest` themselves to control timing precisely, making this a self-contained, repeatable attack requiring no privileged access.

### Recommendation
Enforce a real minimum-output parameter on the internal `swapExactTokensForTokens` call in `_convertFor` (not hardcoded `0`), derived independently from a manipulation-resistant reference (e.g., a TWAP or a governance-configured max-slippage bound relative to `1:1`), rather than trusting the same-block spot price used to decide `convertRatio`.

### Proof of Concept
1. Attacker observes `WombatStaking` configured with an `isMWOM` fee and `smartWomConverter` set.
2. Attacker swaps a large amount of mWOM→WOM (or WOM→mWOM) on the `womMWomPool` to drive `currentRatio()` below `buybackThreshold` and/or skew the effective swap price.
3. Attacker calls `WombatStaking.harvest(lpToken)` on any active pool that has accrued WOM rewards, triggering `_sendRewards` → `smartConvert(feeAmount, 0)`.
4. `_convertFor` executes `swapExactTokensForTokens(..., 0, ...)` at the manipulated price, sending an inflated `amountRec` value out of the WOM fee pool relative to fair value.
5. Attacker reverses the initial swap in the same or next block, netting the price-impact spread extracted from the protocol's buyback, at the expense of the reward pool's unclaimed yield.

### Citations

**File:** wombat/WombatStaking.sol (L739-753)
```text
                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
                            IERC20(wom).safeApprove(mWom, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IMWom(mWom).deposit(feeAmount);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        }
                    }
```

**File:** wombat/SmartWomConvert.sol (L107-146)
```text
    function currentRatio() public view returns (uint256) {
        address[] memory tokenPath = new address[](2);
        tokenPath[0] = mWom;
        tokenPath[1] = wom;
        
        address[] memory poolPath = new address[](1);
        poolPath[0] = womMWomPool;
    
        (uint256 amountOut, ) = IWombatRouter(router).getAmountOut(tokenPath, poolPath, 1e18);
        return amountOut * DENOMINATOR / 1e18;
    }

    /* ============ External Functions ============ */

    function convert(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, msg.sender, _mode);
    }

    function convertFor(uint256 _amountIn, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        external
        returns (uint256 obtainedmWomAmount)
    {
        obtainedmWomAmount = _convertFor(_amountIn, _convertRatio, _minRec, _for, _mode);
    }

    // should mainly used by wombat staking upon sending wom
    function smartConvert(uint256 _amountIn, uint256 _mode) external returns (uint256 obtainedmWomAmount) {
        if (_amountIn == 0) revert MustNoBeZero();

        uint256 convertRatio = DENOMINATOR;
        uint256 mWomToWom = currentRatio();

        if (mWomToWom < buybackThreshold) {
            uint256 maxSwap = maxSwapAmount();
            uint256 amountToSwap = _amountIn > maxSwap ? maxSwap : _amountIn;
            uint256 convertAmount = _amountIn - amountToSwap;
            convertRatio = convertAmount * DENOMINATOR / _amountIn;
        }

        return _convertFor(_amountIn, convertRatio, _amountIn, msg.sender, _mode);
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
