## Title
Zero-Slippage Caller-Controlled `_minMGPRec` in `ArbWomUp2._bullMGP()` Swap of Protocol BUSD Reserves Enables Sandwich-Attack Theft - (File: `wombat/ArbWomUp2.sol`)

### Summary
`ArbWomUp2.incentiveDeposit()` lets any ordinary wallet trigger a swap of the contract's own protocol-owned BUSD reward reserve for MGP through PancakeRouter, using a caller-supplied minimum-output parameter that can be set to `0`. This is the same bug class as the reported `VoltBurn.buyNSendToVoltTreasury()` issue: an unprivileged caller fully controls the slippage-protection value on a swap of pooled/protocol funds, enabling a classic sandwich attack.

### Finding Description
`incentiveDeposit()` is `external`, callable by any EOA, and accepts a caller-supplied `_minMGPRec` with no lower bound enforcement: [1](#0-0) 

When `_bullMode` is true, this value flows unmodified into `_bullMGP()`, which swaps `rewardToSend` (an amount of **BUSD taken from the contract's own balance**, not from the caller's deposit) for MGP via `ROUTER.swapExactTokensForTokens`, passing `_minRec` (i.e. attacker-chosen, can be `0`) as the `amountOutMin`: [2](#0-1) 

The BUSD being swapped is capped by `IERC20(busd).balanceOf(address(this))` — the protocol's finite incentive reserve, exactly analogous to the Shogun balance swapped in `VoltBurn.buyNSendToVoltTreasury()`: [3](#0-2) 

Since `_minMGPRec` can be `0`, a caller can:
1. Manipulate the BUSD/MGP pair on the router beforehand (push the price so BUSD buys more MGP than fair value).
2. Call `incentiveDeposit(_amount, 0, true)`, causing the protocol's BUSD reserve to be converted to MGP at the manipulated, unfairly favorable rate, with the resulting MGP amount (plus `bullBonusRatio` bonus) locked to the caller's own `vlMGP` position.
3. Reverse the initial manipulation, capturing the arbitrage while draining excess value from the protocol's BUSD reserve/MGP pool relative to fair execution.

This mirrors the root cause identified in the report: a user-settable, unbounded (including zero) slippage parameter on a swap of funds that are not exclusively the caller's own, executed atomically with attacker-controlled pool state.

### Impact Explanation
The protocol's BUSD incentive reserve (`busd` balance held by `ArbWomUp2`) can be depleted faster and MGP over-issued/locked to an attacker beyond the intended, fairly-priced reward amount, at the direct expense of protocol reserves — a direct theft of protocol funds reachable by any ordinary wallet with no privileged role required.

### Likelihood Explanation
`incentiveDeposit()` has no access control beyond `whenNotPaused` and `_checkAmount`, so any EOA holding WOM to deposit can trigger `_bullMGP` with `_minMGPRec = 0`. Executing a sandwich requires only same-block front-run/back-run transactions against the PancakeRouter pair, which is a well-understood, cheap, and repeatable attack pattern (as demonstrated in the original report's numeric example).

### Recommendation
Do not allow the caller to freely set `_minMGPRec` to an arbitrarily low/zero value for swaps involving protocol-owned funds. Either compute the minimum output on-chain from a TWAP/oracle-derived expected price with a bounded slippage tolerance (e.g., enforce a maximum allowed slippage such as 1–2%), or remove caller control over this parameter entirely and have the contract compute it internally.

### Proof of Concept
1. Attacker observes `ArbWomUp2` holds a non-trivial BUSD balance (`busdleft`) and PancakeRouter has a BUSD/MGP pool with moderate liquidity.
2. Attacker sells MGP into the BUSD/MGP pool (increasing MGP supply, depressing MGP price relative to BUSD) in a front-running transaction.
3. Attacker calls `incentiveDeposit(_amount, 0, true)` with `_minMGPRec = 0`; `_bullMGP` swaps `rewardToSend` BUSD for MGP at the manipulated (favorable) rate, and the resulting inflated MGP amount (times `1 + bullBonusRatio`) is locked to the attacker via `vlMGP.lockFor`.
4. Attacker buys back MGP with the BUSD obtained in step 2, restoring the pool price and completing the round trip, netting more value than deployed while draining the protocol's BUSD reserve faster than intended and locking excess MGP rewards to themselves. [2](#0-1)

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

**File:** wombat/ArbWomUp2.sol (L99-117)
```text
    function getRewardAmount(uint256 _amount, address _account) external view returns (uint256) {
        if (_amount == 0 || rewardMultiplier.length == 0) return 0;
        uint256 accumulated = _amount + userWOMDeposited[_account];

        uint256 rewardAmount = 0;
        uint256 i = 1;
        while (i < rewardTier.length && accumulated > rewardTier[i]) {
            rewardAmount +=
                (rewardTier[i] - rewardTier[i - 1]) *
                rewardMultiplier[i - 1];
            i++;
        }
        rewardAmount += (accumulated - rewardTier[i - 1]) * rewardMultiplier[i - 1];

        uint256 busdReward = (rewardAmount / DENOMINATOR) - this.calDoubledCounted(_account);
        uint256 busdleft = IERC20(busd).balanceOf(address(this));

        return busdReward > busdleft ? busdleft : busdReward;
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
