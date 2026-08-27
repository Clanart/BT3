### Title
Unprotected zero-slippage buyback swap in `SmartWomConvert` lets an attacker sandwich the permissionless `WombatStaking.harvest()` reward-conversion path - (File: `wombat/SmartWomConvert.sol`)

### Summary
`WombatStaking.harvest()` is callable by any address and internally triggers a WOM→mWom conversion of protocol fee revenue through `SmartWomConvert.smartConvert()`. When the conversion decides to "buy back" mWom through the Wombat `wom`↔`mWom` AMM pool, it executes the swap with a hardcoded minimum-output of `0`, giving zero slippage protection. Because the decision to swap (and how much to swap) is itself driven by the same pool's live spot state (`currentRatio()` / `maxSwapAmount()`), an attacker can manipulate the pool immediately before calling `harvest()` and sandwich the zero-slippage swap to extract value from the protocol's fee stream at the expense of stakers/mWom backing.

### Finding Description
`WombatStaking.harvest(_lpToken)` has no caller restriction beyond `_onlyActivePool`, so it is reachable by any unprivileged wallet: [1](#0-0) 

It calls `_toMasterWomAndSendReward` → `_sendRewards`, which — for the WOM reward portion — routes the fee amount into `smartWomConverter.smartConvert(feeAmount, 0)` whenever a smart converter is configured: [2](#0-1) 

Inside `SmartWomConvert.smartConvert`, the decision of how much WOM to route through the wom/mWom AMM pool (instead of minting mWom 1:1) is based entirely on the pool's live spot price (`currentRatio()`) and the pool's current cash/liability imbalance (`maxSwapAmount()`), both of which are read directly from the mutable, unmanipulated-by-TWAP `IWombatRouter`/`IAsset` state: [3](#0-2) 

When the buyback branch is taken, `_convertFor` executes the actual swap with a hardcoded minimum output of `0`, i.e. no slippage protection whatsoever for this internal, protocol-initiated swap: [4](#0-3) 

Because both (a) the trigger condition/amount for the swap and (b) the swap execution itself rely on the pool's instantaneous state with zero minimum-output enforcement, an attacker can:
1. Trade against the wom/mWom pool to push `currentRatio()` below `buybackThreshold` and/or skew `cash`/`liability` so `maxSwapAmount()` is large and unfavorable for the protocol side.
2. Call the permissionless `WombatStaking.harvest()` (or wait for any user/keeper to do so, e.g., via a deposit/withdraw that also triggers `_toMasterWomAndSendReward`), forcing `smartConvert` to swap protocol-owned WOM for mWom at the manipulated, unfavorable rate with `minAmountOut = 0`.
3. Reverse the initial trade in the same transaction/block, extracting the value difference — a textbook flash-loan/sandwich price-manipulation pattern identical in class to the referenced MU&MUG exploit, where an unprotected on-chain conversion trusted manipulable spot AMM state.

### Impact Explanation
Every time protocol fee revenue is harvested and routed through `smartConvert`, real WOM value belonging to the protocol/stakers can be swapped away for far less mWom than fair value, with the difference captured by the attacker. Because this fee-routing path executes on essentially every harvest/deposit/withdraw across all Wombat pools registered in `WombatStaking`, this is a recurring, protocol-wide value leak — a direct theft of protocol/staker-designated yield and a degradation of mWom backing (protocol insolvency risk for the mWom peg), not a one-off loss confined to the attacker's own funds.

### Likelihood Explanation
`harvest()` requires no privilege and no special conditions besides an active pool, and it is also triggered implicitly by ordinary deposit/withdraw flows through `_toMasterWomAndSendReward`. The only requirement for the attacker is enough capital (or a flash loan) to move the wom/mWom pool's spot price/imbalance within one block, which is exactly the bug class demonstrated in the referenced report. The `minAmountOut = 0` on the buyback swap removes the one guard that would normally bound the loss, making this straightforward to trigger opportunistically whenever pool liquidity is thin relative to harvestable fee size.

### Recommendation
- Never pass `0` as the minimum output for the internal buyback swap in `SmartWomConvert._convertFor`; compute an acceptable minimum from a manipulation-resistant reference (e.g., a TWAP, oracle, or a bounded percentage of `estimateTotalConversion`) and pass it as `minAmountOut`.
- Avoid using the same pool's instantaneous `cash()`/`liability()`/router quote both to decide *whether/how much* to swap and to execute the swap without any independent sanity check; add a maximum allowed price-impact/deviation check before committing to the buyback branch.
- Consider restricting or rate-limiting how much of a single `harvest()` call's WOM can be routed through the AMM swap path per block, or require the caller supply a `minRec` that is enforced end-to-end even for the internally-triggered `smartConvert` path.

### Proof of Concept
Conceptual sequence (values illustrative):
1. Attacker holds WOM and mWom (or flash-loans them) and swaps a large amount of mWom into the `womMWomPool` via the Wombat router, depressing the pool's spot mWom price and/or skewing `cash()`/`liability()` of the `wom` asset so that `SmartWomConvert.currentRatio() < buybackThreshold` and `maxSwapAmount()` is large.
2. Attacker (or any third party) calls `WombatStaking.harvest(_lpToken)` for a pool with pending WOM rewards and a fee configured with `isMWOM = true`; this routes the WOM fee amount into `smartWomConverter.smartConvert(feeAmount, 0)`.
3. `smartConvert` observes the manipulated `currentRatio()`, enters the buyback branch, and calls `IWombatRouter.swapExactTokensForTokens(..., 0, ...)` — swapping real protocol WOM for mWom at the manipulated rate with no floor on the amount received.
4. Attacker reverses their initial trade in the same transaction, restoring the pool and pocketing the WOM/mWom spread extracted from the protocol's swap.

I could not locate the concrete `IWombatRouter` interface/implementation file in this index (only used via `wombat/SmartWomConvert.sol` imports) to confirm the exact parameter ordering beyond what's shown in the citation above; this is a minor gap that does not affect the root-cause finding, since the `0` literal passed as `_minimumreceiveAmount`-equivalent argument is unambiguous in `wombat/SmartWomConvert.sol:194-196`.

### Citations

**File:** wombat/WombatStaking.sol (L329-335)
```text
    /// @notice harvest a Pool from Wombat
    /// @param _lpToken wombat pool lp as helper identifier
    function harvest(
        address _lpToken
    ) whenNotPaused _onlyActivePool(_lpToken) external {
        _toMasterWomAndSendReward(_lpToken, 0, true); // triggers harvest from wombat exchange
    }
```

**File:** wombat/WombatStaking.sol (L738-753)
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

**File:** wombat/SmartWomConvert.sol (L98-117)
```text
    function maxSwapAmount() public view returns (uint256) {
        uint256 womCash = IAsset(womAsset).cash();
        uint256 womLiability = IAsset(womAsset).liability();
        if (womCash >= womLiability)
            return 0;

        return (womLiability - womCash) * ratio / DENOMINATOR;
    }

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
