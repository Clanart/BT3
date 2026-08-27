Confirmed: `harvest()` is permissionless (only `whenNotPaused` + `_onlyActivePool`), so an attacker can trigger `_sendRewards` themselves in the same transaction sequence as their pool manipulation.### Title
Zero-slippage AMM swap in `smartConvert()` fee-conversion path enables sandwich extraction of protocol fee value - ([File: wombat/SmartWomConvert.sol], via `WombatStaking._sendRewards`)

### Summary
When `WombatStaking._sendRewards` converts the `isMWOM`/WOM fee via `IConverter(smartWomConverter).smartConvert(feeAmount, 0)`, the underlying AMM swap in `SmartWomConvert._convertFor` is executed with `amountOutMin = 0`. Because `harvest()`/`vote()` are permissionless and the `womMWomPool` spot price is attacker-manipulable, an unprivileged actor can sandwich this conversion to reduce the mWom actually received by the fee recipient (`feeInfo.to`), extracting the difference as arbitrage profit.

### Finding Description
`WombatStaking._sendRewards` (called from `_toMasterWomAndSendReward`, reachable via the permissionless `harvest()` and via `vote()`/`castVotes()`) computes a fee and, for the `isMWOM && rewardToken == wom` branch, calls: [1](#0-0) 

`smartConvert()` decides how much of `feeAmount` to route through the `womMWomPool` AMM versus a direct 1:1 mint, based on a spot-price check (`currentRatio()`, quoted for only `1e18` units) against `buybackThreshold`: [2](#0-1) 

The actual swap executed on the (potentially much larger) `buybackAmount` passes `0` as the minimum output, with no slippage protection whatsoever: [3](#0-2) 

Because `harvest(address _lpToken)` has no access restriction beyond `whenNotPaused` and `_onlyActivePool`, any unprivileged address can call it directly: [4](#0-3) 

This means an attacker does not even need to "front-run" a third party's transaction — they can, in a single sequence of self-controlled transactions, (1) trade against `womMWomPool` to move its price, (2) call `harvest()` (or wait for/front-run `vote()`) to force `_sendRewards` → `smartConvert` to execute the zero-slippage swap against the manipulated pool, and (3) reverse their initial trade to restore price and realize arbitrage profit funded by the mispriced swap. The `currentRatio()`/`buybackThreshold` gate only decides *whether* the AMM path executes (using a 1e18-unit spot quote), but does not bound the *execution price* of the actual `buybackAmount` swap, so it provides no real protection once the branch is entered.

Note on the specific "backing ratio" framing in the question: the swap path does not mint new mWom (only the non-swap `IMWom(mWom).deposit(convertAmount)` branch mints), so this exploit does not directly change mWom's supply/backing invariant. The concrete, verifiable loss is that `feeTosend` (mWom forwarded via `queueNewRewards` to `feeInfo.to`) can be made smaller than the fair-value conversion of `feeAmount` WOM, i.e. theft of a portion of the protocol's harvested fee/yield, funded by the AMM slippage the attacker engineers and captures.

### Impact Explanation
This is a value-extraction/theft-of-yield vulnerability: an unprivileged attacker can cause the WOM fee that should be converted into mWom for `feeInfo.to` (a reward pool receiving `queueNewRewards`) to be swapped at an artificially bad rate, capturing the difference themselves. This matches the "theft of unclaimed yield" impact category, and is repeatable on every harvest/vote call where this fee branch is active and `smartWomConverter` is set, so losses can accumulate over time.

### Likelihood Explanation
Preconditions: `smartWomConverter` must be configured and an active `isMWOM` fee entry with `rewardToken == wom` must exist; `currentRatio() < buybackThreshold` must hold so the AMM branch is taken (attacker can engineer this by trading in `womMWomPool`); attacker needs capital/flash-loan access to move the pool and enough liquidity depth advantage to profit net of AMM fees and gas. Since `harvest()` is fully permissionless, the attacker controls the entire sequence (manipulate → harvest → reverse) without needing to wait on or guess a third-party transaction, making this a self-contained, repeatable MEV-style attack rather than a passive front-run.

### Recommendation
Add real slippage protection to the buyback swap in `SmartWomConvert._convertFor`/`smartConvert`, e.g. compute a `minRec` from a time-weighted or manipulation-resistant price (not a same-block 1e18 spot quote) and pass it as `amountOutMin` to `swapExactTokensForTokens` instead of `0`. Consider also sizing `maxSwapAmount` more conservatively relative to available pool depth and enforcing a maximum acceptable price impact per swap.

### Proof of Concept
Foundry/Hardhat fork test plan:
1. Fork BSC at a block where `WombatStaking`, `SmartWomConvert`, and the live `womMWomPool` are deployed and `smartWomConverter` is set with an active `isMWOM` fee.
2. As attacker EOA: flash-loan/acquire WOM and mWom, execute a large trade against `womMWomPool` to push `currentRatio()` below `buybackThreshold` while keeping the pool in a state that will cause a large negative price impact for a subsequent WOM→mWom swap of size `feeAmount`.
3. Call `WombatStaking.harvest(lpToken)` (permissionless) to trigger `_sendRewards` → `smartConvert(feeAmount, 0)`, and record the actual `feeTosend` (mWom) forwarded to `feeInfo.to` via the `RewardPaidTo`/`queueNewRewards` event.
4. Reverse the attacker's initial trade in `womMWomPool` and measure attacker's net WOM/mWom profit.
5. Assert: `feeTosend` obtained is materially less than `estimateTotalConversion(feeAmount, convertRatio)` computed pre-manipulation, and attacker's post-trade balance shows positive profit — demonstrating value extracted from the fee at the fee recipient's expense.

### Citations

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
