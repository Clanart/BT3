### Title
Missing slippage/price protection in `ArbWomUp2._bullMGP` allows attacker to self-sandwich the BUSD→MGP swap and extract inflated vlMGP-locked value from the reward pool - (File: wombat/ArbWomUp2.sol)

### Summary
`incentiveDeposit(_amount, _minMGPRec, true)` lets the caller freely choose `_minMGPRec`, which is passed unchecked as the `amountOutMin` of `ROUTER.swapExactTokensForTokens` inside `_bullMGP`. Because there is no oracle/TWAP-based minimum-output enforcement, a caller can set `_minMGPRec = 0`, manipulate the BUSD/MGP pool reserves immediately before the swap, and restore them afterward, causing `amounts[1]` (and therefore `mgpAmountToLcok`) to be computed off an artificially favorable price rather than the fair market rate.

### Finding Description
`incentiveDeposit` computes a fixed, tier-based `rewardToSend` (denominated in BUSD) via `getRewardAmount`, independent of any AMM price [1](#0-0) . When `_bullMode` is `true`, this BUSD amount is routed into `_bullMGP`, which swaps it for MGP through `ROUTER.swapExactTokensForTokens(_busdAmount, _minRec, ...)` and then locks `mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR` as vlMGP for the caller [2](#0-1) .

`_minRec` here is exactly `_minMGPRec`, the caller-supplied parameter from `incentiveDeposit` [3](#0-2) . There is no protocol-side floor (e.g., comparison against a TWAP or last-known fair price) — the only check is the AMM router's own `amountOutMin`, which the attacker controls and can set to `0`. This means the swap's execution price is entirely determined by the current pool spot price at call time, with no protection against manipulation.

An attacker can therefore:
1. Trade against the BUSD/MGP pool (e.g., sell MGP for BUSD) to depress the MGP price / inflate MGP reserves relative to BUSD in that pool.
2. Call `incentiveDeposit(_amount, 0, true)`, causing the protocol's fixed BUSD reward to be swapped through the manipulated pool, yielding an inflated `amounts[1]` and thus inflated `mgpAmountToLcok`, which is locked to the attacker's vlMGP position.
3. Reverse the initial trade to restore the pool price, in the same block (self-sandwich, e.g., via a second transaction or a searcher-style multi-tx bundle).

Because `nonReentrant`/`whenNotPaused` only guard against reentrancy/pausing and do not constrain external price manipulation via separate router calls, none of the existing modifiers stop this. The vulnerability is a classic missing-slippage-protection / price-manipulation issue: the contract delegates a value-defining parameter (`_minMGPRec`) fully to the untrusted caller instead of enforcing a protocol-controlled bound.

Note: the question references `rewards/BNBZapper.sol`, but no `incentiveDeposit`/`_bullMGP`/swap logic exists in that file; the actual vulnerable code is in `wombat/ArbWomUp2.sol` as cited above.

### Impact Explanation
This enables theft of unclaimed protocol yield: the attacker locks more MGP (backed by the BUSD/MGP liquidity pool) into their own vlMGP position than the fair-market-price equivalent of their earned BUSD reward, at the expense of the pool's MGP reserves/liquidity providers. This matches the "theft of unclaimed yield" / economic-loss impact class in scope.

### Likelihood Explanation
The attack requires only unprivileged capital to move the BUSD/MGP pool price temporarily (flash-loanable if the pool supports it, or self-funded), a qualifying WOM deposit to be eligible for a nonzero `busdReward` via `getRewardAmount`, and the ability to sequence transactions within/around the same block (self-sandwich). No special privileges are needed, and the attack is repeatable each time the attacker (or any user) deposits with `_bullMode = true` and thin pool liquidity, making it feasible whenever pool depth is small relative to the reward size.

### Recommendation
Do not let the caller fully control the AMM slippage bound for a value-defining conversion. Enforce a protocol-side minimum output derived from a manipulation-resistant price source (e.g., a TWAP oracle or a bounded deviation from a recently observed price), and/or cap `_minMGPRec` to be no lower than that computed floor rather than accepting a caller-supplied value directly, including disallowing `0`.

### Proof of Concept
Foundry test plan:
1. Deploy `ArbWomUp2` with a mock/fork PancakeRouter and a BUSD/MGP pool with realistic (thin) liquidity.
2. Fund the contract with BUSD and set up reward tiers so a test user qualifies for a known `busdReward`.
3. Baseline: call `incentiveDeposit(amount, minMGPRec=fairMGPOut, true)` at unmanipulated pool price; record `mgpAmountToLcok`.
4. Attack: (a) attacker trades against the pool to shift reserves favorably, (b) attacker calls `incentiveDeposit(amount, 0, true)` in the manipulated state, capturing `mgpAmountToLcok`, (c) attacker reverses the initial trade to restore pool price.
5. Assert `mgpAmountToLcok` from step 4 exceeds the fair-price-equivalent baseline from step 3 by more than normal AMM fee/slippage bounds, and that the attacker's net BUSD spend (fees) is smaller than the value of the excess locked MGP — demonstrating profitable yield extraction enabled by `_minMGPRec = 0`.

### Citations

**File:** wombat/ArbWomUp2.sol (L82-97)
```text
    function incentiveDeposit(
        uint256 _amount, uint256 _minMGPRec, bool _bullMode
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        if (_amount == 0) return;

        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        if (_bullMode) {
            _bullMGP(rewardToSend, _minMGPRec, msg.sender);
        } else {
            IERC20(busd).transfer(msg.sender, rewardToSend);
            emit BUSDRewarded(msg.sender, rewardToSend);
        }
    }
```

**File:** wombat/ArbWomUp2.sol (L162-181)
```text
    function _bullMGP(uint256 _busdAmount, uint256 _minRec, address _account) internal {
        IERC20(busd).safeApprove(address(ROUTER), _busdAmount);
        
        address[] memory path = new address[](2);
        path[0] = busd;
        path[1] = mgp;
        uint256[] memory amounts = ROUTER.swapExactTokensForTokens(
            _busdAmount,
            _minRec,
            path,
            address(this),
            block.timestamp
        );

        uint256 mgpAmountToLcok = amounts[1] * (DENOMINATOR + bullBonusRatio) / DENOMINATOR; // get bull mode bonus
        IERC20(mgp).approve(address(vlMGP), mgpAmountToLcok);
        vlMGP.lockFor(mgpAmountToLcok, _account);

        emit VLMGPRewarded(_account, _busdAmount, mgpAmountToLcok);
    }
```
