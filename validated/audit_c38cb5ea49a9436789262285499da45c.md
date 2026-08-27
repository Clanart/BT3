### Title
Hardcoded zero minimum-output in `ArbWomUp3._deposit` removes slippage protection on WOM→mWom swap, exposing depositors to MEV sandwich losses - (File: `wombat/ArbWomUp3.sol`)

### Summary
`ArbWomUp3.incentiveDeposit` (mode 2) routes half of a depositor's WOM through `SmartWomConvert.convert` with the minimum-received parameter hardcoded to `0`, which combined with `SmartWomConvert`'s own hardcoded `0` minimum-amount-out on the underlying Wombat router swap, eliminates all slippage protection for the swapped portion of user funds.

### Finding Description
`incentiveDeposit` is a public, unprivileged entry point that any user can call to deposit WOM and receive vlMGP/mWomSV rewards: [1](#0-0) 

When `_mode == 2`, it calls the internal `_deposit`, which splits the user's WOM in half and routes the "toSwap" half through `IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0)`, hardcoding the `_minRec` argument to `0`: [2](#0-1) 

This flows into `SmartWomConvert._convertFor`, which itself performs the actual Wombat pool swap with a hardcoded `0` minimum output regardless of the caller-supplied `_minRec`: [3](#0-2) 

The only slippage safeguard is the post-swap check `if (convertAmount + amountRec < _minRec) revert MinRecNotMatch();`, but since `ArbWomUp3` always passes `_minRec = 0`, this check can never fail — there is no protection whatsoever against a bad execution price on the buyback swap. If the caller also controls `_convertRatio` (passed by the user to `incentiveDeposit`), setting it to `0` forces the entire "toSwap" amount through the unprotected swap.

### Impact Explanation
An MEV bot observing a pending `incentiveDeposit(_amount, _convertRatio, false, 2)` transaction can sandwich the `swapExactTokensForTokens` call inside `SmartWomConvert._convertFor`, since it has no minimum-out enforcement. This results in direct, concrete theft of the depositing user's WOM value — up to the full value of the swapped WOM portion in a highly imbalanced pool state, with no on-chain check to prevent or revert the loss.

### Likelihood Explanation
Any unprivileged wallet calling `incentiveDeposit` with `_mode == 2` (a normal, documented usage path of this airdrop/incentive contract) is exposed. No privileged role or special conditions are required — only a public mempool transaction that an MEV bot can detect and sandwich.

### Recommendation
Do not hardcode `_minRec` to `0` in `ArbWomUp3._deposit`. Instead, thread a user-supplied minimum-received parameter from `incentiveDeposit` through to `IConverter(smartWomConvert).convert`. Additionally, fix `SmartWomConvert._convertFor` to pass a real minimum-amount-out (derived from `_minRec`/expected pool price) into `IWombatRouter.swapExactTokensForTokens` rather than hardcoding `0`, so slippage is checked at the point of execution rather than only via a post-hoc balance comparison that the caller can neutralize by supplying `_minRec = 0`.

### Proof of Concept
1. Attacker monitors mempool for a call to `ArbWomUp3.incentiveDeposit(amount, convertRatio=0, false, 2)`.
2. This triggers `_deposit`, which calls `IConverter(smartWomConvert).convert(toSwap, 0, 0, 0)` — `_minRec = 0`.
3. Inside `SmartWomConvert._convertFor`, since `_convertRatio = 0`, `buybackAmount = toSwap` (the entire swap portion), and the router call `swapExactTokensForTokens(tokenPath, poolPath, buybackAmount, 0, ...)` executes with `amountOutMin = 0`.
4. Attacker front-runs with a large swap in the same direction to move the pool price, lets the victim's swap execute at the manipulated price, then back-runs to restore the price and capture the difference.
5. The post-swap check `convertAmount + amountRec < _minRec` (i.e., `< 0`) never reverts, so the victim's loss is silently accepted and locked into `mWomSV` via `ILocker(mWomSV).lockFor`.

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
