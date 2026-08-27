## Title
Spot-price AMM quote (`getAmountOut`) drives mandatory buyback swap with zero slippage protection in `SmartWomConvert`, allowing WOM reward harvesting/distribution to be griefed via sandwich manipulation - (File: `wombat/SmartWomConvert.sol`)

### Summary
`SmartWomConvert.currentRatio()` and the buyback swap inside `_convertFor` rely on the instantaneous AMM quote returned by `IWombatRouter.getAmountOut`/`swapExactTokensForTokens` against the `womMWomPool` rather than any time-weighted price. This is the same class of bug flagged in the external report (spot price read directly from a pool's live state instead of a manipulation-resistant TWAP source), but reachable here through the unprivileged, permissionless `WombatStaking.harvest()` flow.

### Finding Description
`currentRatio()` reads a single-block spot quote from the `womMWomPool`: [1](#0-0) 

This spot ratio decides, inside `smartConvert`, how much of the incoming WOM must be routed through a live swap ("buyback") versus minted 1:1 via `IMWom.deposit`: [2](#0-1) 

The buyback swap itself is executed with **zero minimum-output protection** (`0` passed as `amountOutMin`), so it fully absorbs whatever price the pool is showing at execution time: [3](#0-2) 

The only safety net is a post-hoc check that the total obtained `mWom` is not less than `_minRec`: [4](#0-3) 

Crucially, when `smartConvert` is invoked (its intended production caller), `_minRec` is hardcoded to the full input amount (`_amountIn`), leaving no tolerance at all: [5](#0-4) 

This function is reachable from any unprivileged wallet through `WombatStaking.harvest()` → `_toMasterWomAndSendReward` → `_sendRewards`, which unconditionally calls `smartConvert` on the WOM fee portion whenever a fee marked `isMWOM` is active and a `smartWomConverter` is configured: [6](#0-5) [7](#0-6) 

`harvest()` has no caller restriction beyond the pool being active and the contract not paused, so it is callable by any wallet: [6](#0-5) 

Because the buyback swap has no slippage floor of its own and the aggregate check demands `obtainedmWomAmount >= _amountIn`, an attacker can cheaply push the `womMWomPool` spot price against the pending buyback (sandwiching the `harvest()` transaction, then reversing the trade in the same block) so that `amountRec` from the swap comes back below what's needed to satisfy the strict `_minRec` check. The whole `_convertFor` call reverts with `MinRecNotMatch()`, which reverts the entire `harvest()`/`_sendRewards` transaction, since Solidity calls revert the full call stack.

### Impact Explanation
Every time a legitimate caller (user, keeper, or protocol automation) invokes `harvest()` on a pool whose reward fee routes through `smartWomConverter`, an attacker who front-runs with a manipulative swap on `womMWomPool` can force the transaction to revert. Because this manipulation is cheap (round-trip swap + fee, reversible in the same block) and repeatable on every attempted harvest, the attacker can persistently block reward harvesting and fee distribution for the affected Wombat pools, freezing the unclaimed WOM/mWOM yield that would otherwise flow to `BaseRewardPool` via `queueNewRewards`.

### Likelihood Explanation
Likelihood is high in the sense that `harvest()` is fully permissionless and callable by anyone, and the manipulation only requires enough capital to move the `womMWomPool` spot price for one block (or use of a flash loan), which the attacker recovers by unwinding the trade. No privileged role is required. The main mitigating factor is that this is a repeated-griefing pattern rather than a one-shot exploit, and the protocol owner can disable the mechanism by setting `smartWomConverter` back to `address(0)` as an operational workaround.

### Recommendation
- Do not use a live `getAmountOut`/spot quote from `womMWomPool` to gate the swap-vs-mint decision; use a time-weighted or otherwise manipulation-resistant reference price for `currentRatio()`.
- Give the buyback swap in `_convertFor` its own bounded slippage parameter instead of `0`, and avoid making the aggregate `_minRec` check in `smartConvert` unconditionally equal to the full input amount, so that transient pool volatility cannot force the entire harvest/reward-distribution transaction to revert.

### Proof of Concept
1. Attacker monitors the mempool for a `WombatStaking.harvest(_lpToken)` call on a pool with an active `isMWOM` fee and `smartWomConverter` set.
2. Attacker front-runs with a large swap on `womMWomPool` that depresses the price `IWombatRouter.getAmountOut` would return for `mWom -> wom` shortly after, worsening the terms for a `wom -> mWom` buyback swap.
3. The victim's `harvest()` transaction executes `_sendRewards` → `smartConvert(feeAmount, 0)` → `_convertFor`, whose buyback swap (`swapExactTokensForTokens` with `amountOutMin = 0`, `wombat/SmartWomConvert.sol:194-196`) returns fewer `mWom` than needed to satisfy `convertAmount + amountRec >= _amountIn` (`wombat/SmartWomConvert.sol:204-205`, with `_minRec = _amountIn` from `wombat/SmartWomConvert.sol:146`).
4. The whole `harvest()` transaction reverts with `MinRecNotMatch()`, and the attacker reverses their manipulative swap in the same block (or backruns it), incurring only pool fees.
5. Attacker repeats this on every subsequent `harvest()` attempt for the pool, indefinitely blocking WOM/mWOM reward distribution to `BaseRewardPool` for that pool.

### Citations

**File:** wombat/SmartWomConvert.sol (L107-117)
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
```

**File:** wombat/SmartWomConvert.sol (L133-147)
```text
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
    }
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

**File:** wombat/SmartWomConvert.sol (L204-205)
```text
        if (convertAmount + amountRec < _minRec)
            revert MinRecNotMatch();
```

**File:** wombat/WombatStaking.sol (L331-335)
```text
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L739-746)
```text
                    if (feeInfo.isMWOM && rewardToken == wom) {
                        if (smartWomConverter != address(0)) {
                            IERC20(wom).safeApprove(smartWomConverter, feeAmount);
                            uint256 beforeBalnce = IMWom(mWom).balanceOf(address(this));
                            IConverter(smartWomConverter).smartConvert(feeAmount, 0);
                            rewardToken = mWom;
                            feeTosend = IMWom(mWom).balanceOf(address(this)) - beforeBalnce;
                        } else {
```
