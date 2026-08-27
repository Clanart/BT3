## Finding Analysis

The mechanism described is directly supported by the code in `wombat/WombatStaking.sol` and `wombat/SmartWomConvert.sol`.

### Confirmed code path

- `harvest(address _lpToken)` is `external`, gated only by `whenNotPaused` and `_onlyActivePool`, with no operator/role restriction — any EOA can call it for an active pool. [1](#0-0) 

- `harvest` calls `_toMasterWomAndSendReward(_lpToken, 0, true)`, which triggers a `deposit(pid, 0)` on `masterWombat` (auto-claims pending WOM rewards), computes `womRewards`, then calls `_sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards)`. [2](#0-1) 

- The exact same `_toMasterWomAndSendReward` → `_sendRewards` path is invoked from `deposit`, `depositLP`, and `withdraw` — i.e. it sits on every principal deposit/withdrawal for that pool, not just harvest. [3](#0-2) [4](#0-3) [5](#0-4) 

- Inside `_sendRewards`, when a fee entry has `isMWOM == true` and the reward token is `wom`, and a `smartWomConverter` is configured, the code calls `IConverter(smartWomConverter).smartConvert(feeAmount, 0)` with no try/catch — a revert here reverts the entire outer transaction. [6](#0-5) 

- `smartConvert` hardcodes `_minRec = _amountIn` (0% slippage tolerance) when calling `_convertFor`. [7](#0-6) 

- In `_convertFor`, when `currentRatio()` (mWom-per-WOM price from the WOM/mWom pool) is below `buybackThreshold`, part of the amount is routed through an on-chain swap (`swapExactTokensForTokens` with `amountOutMin = 0`) whose actual output depends on live pool state; if `convertAmount + amountRec < _minRec`, the call reverts with `MinRecNotMatch`. [8](#0-7) 

So the chain is real: an attacker who can move `currentRatio()`/the WOM-mWom pool price (a public AMM, manipulable with enough capital, e.g. via swaps or flash loans in that pool) can make `smartConvert` revert, and because `smartConvert` is unconditionally called inside `_sendRewards` on the hot path of `deposit`, `depositLP`, `withdraw`, and `harvest`, this reverts those user-facing operations for the affected pool as well.

### Caveats on the stated preconditions

- The claimed precondition "the bonus reward token registered for the asset is also one of the fee currencies" is not actually required by the code. `_sendRewards` only routes through `smartConvert` when `feeInfo.isMWOM && rewardToken == wom`; bonus tokens (`assetToBonusRewards`) go through the same fee loop but never trigger the `mWom`-conversion branch unless the bonus token itself literally is `wom`. This detail in the question does not match the code and is unsupported. [9](#0-8) 
- Reaching the revert condition requires the attacker to actively manipulate `currentRatio()` below `buybackThreshold` in the WOM/mWom pool AND ensure the resulting swap output is below `amountToSwap` — this is possible with capital via price manipulation of that pool, but is not a persistent, one-shot break; it requires continuously maintaining an adverse price for as long as the attacker wants to block operations, since normal AMM arbitrage would otherwise restore favorable pricing.
- There is a low-cost admin mitigation: `setSmartConvert(address(0))` immediately bypasses the vulnerable code path (falling back to plain `IMWom(mWom).deposit`), and `setPoolRewardFeeFree` / `setFee` can also neutralize it, meaning a "temporary freezing of funds for at least 24 hours" is not guaranteed unless the attacker can economically sustain manipulation against arbitrage and admin response — this weakens (but does not eliminate) the claimed Immunefi High classification.

### Verdict

The core mechanism — `smartConvert`'s zero-slippage-tolerance (`_minRec == _amountIn`) reachable unconditionally from `deposit`/`depositLP`/`withdraw`/`harvest` via `_sendRewards`, with no try/catch, causing a revert-based DoS on principal operations when the WOM/mWom pool price is adversarially moved — is real and code-supported.

### Title
Unbounded `smartConvert` revert in `_sendRewards` causes DoS of deposits/withdrawals via WOM/mWom price manipulation - (File: wombat/WombatStaking.sol)

### Summary
`_sendRewards`, called unconditionally from `deposit`, `depositLP`, `withdraw`, and `harvest` in `WombatStaking.sol`, invokes `SmartWomConvert.smartConvert` with a hardcoded zero-slippage `_minRec = _amountIn`. Because the swap leg of `smartConvert` depends on the live price of the WOM/mWom pool, an attacker who manipulates that pool's price can force `MinRecNotMatch` reverts, which propagate up and block all deposit/withdraw operations for the pool.

### Finding Description
`harvest(address _lpToken)` (and `deposit`, `depositLP`, `withdraw`) call `_toMasterWomAndSendReward`, which calls `_sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards)`. When an `isMWOM` fee is active and `smartWomConverter` is set, `_sendRewards` calls `smartConvert(feeAmount, 0)` without try/catch. `smartConvert` sets `_minRec = _amountIn` (0% tolerance) and, when `currentRatio() < buybackThreshold`, executes an on-chain swap via `IWombatRouter.swapExactTokensForTokens` with `amountOutMin = 0`; if the resulting `convertAmount + amountRec` value is less than `_amountIn`, `_convertFor` reverts with `MinRecNotMatch`, which is uncaught and bubbles up through `_sendRewards` → `_toMasterWomAndSendReward` → `harvest`/`deposit`/`depositLP`/`withdraw`, reverting the entire user transaction. Existing modifiers (`whenNotPaused`, `_onlyActivePool`, `nonReentrant`) do not guard against this because the revert is a legitimate code path, not a reentrancy or pause bypass.

### Impact Explanation
While the pool's WOM/mWom AMM price is kept unfavorable by the attacker, every `deposit`, `depositLP`, `withdraw`, and `harvest` call for pools where an active `isMWOM` fee routes through `smartConvert` will revert, freezing principal deposit/withdrawal functionality for that pool. This matches a temporary freezing-of-funds impact, though the duration and severity depend on the attacker's ability to sustain the manipulated price against market arbitrage and against a fast admin fix (`setSmartConvert(address(0))`, `setFee`, or `setPoolRewardFeeFree`), which weakens confidence in a guaranteed 24-hour-plus freeze.

### Likelihood Explanation
Exploitability requires: (1) an active `isMWOM` fee configured and `smartWomConverter != address(0)` (admin configuration, plausible in production), and (2) the attacker having sufficient capital to move `currentRatio()` of the WOM/mWom pool below `buybackThreshold` and to make the subsequent swap yield less than the swapped-in amount. This is capital-intensive and must be sustained repeatedly to block transactions over time, and is easily mitigated by the contract owner disabling `smartWomConverter`. The claimed precondition about bonus reward tokens being fee currencies is not supported by the code and is not actually required.

### Recommendation
Wrap the `smartConvert` call in `_sendRewards` in a try/catch (or a low-level call with a success check) and fall back to the non-smart `IMWom(mWom).deposit` path (or skip the mWom conversion for that harvest and leave the WOM fee unconverted) on failure, so a manipulable external swap price can never block core deposit/withdraw accounting. Additionally, consider passing a real slippage tolerance instead of `_minRec == _amountIn` inside `smartConvert`.

### Proof of Concept
Foundry fork test plan:
1. Fork mainnet/BSC at a block where a Wombat pool is registered in `WombatStaking` with an active `isMWOM` fee and `smartWomConverter` set to a live `SmartWomConvert` deployment.
2. Snapshot `IERC20(poolInfo.lpAddress).balanceOf(address(this))` for `WombatStaking` and note current receipt token supply.
3. As an unprivileged attacker EOA, manipulate the WOM/mWom pool (via `swapExactTokensForTokens` on the `womMWomPool`) to push `currentRatio()` below `buybackThreshold` and simultaneously push the wom→mWom swap output below the swapped-in amount.
4. Call `harvest(_lpToken)` (or trigger a `deposit`/`withdraw` through the pool helper) and assert it reverts with `MinRecNotMatch`.
5. Assert that legitimate user `deposit`/`withdraw` calls on the same pool revert for as long as the manipulated price persists, and that `IERC20(poolInfo.lpAddress).balanceOf(address(this))` vs receipt token supply remains unaffected only because the transaction never completes (i.e., confirm no double-crediting, just a DoS).
6. Additionally verify that calling `setSmartConvert(address(0))` (owner action) immediately restores functionality, to assess real freeze duration.

### Citations

**File:** wombat/WombatStaking.sol (L242-270)
```text
    function deposit(
        address _lpAddress,
        uint256 _amount,
        uint256 _minimumLiquidity,
        address _for,
        address _from
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];
        address depositToken = poolInfo.depositToken;
        IERC20(depositToken).safeTransferFrom(_from, address(this), _amount);

        IERC20(depositToken).safeApprove(poolInfo.depositTarget, _amount);
        uint256 beforeBalance = IERC20(poolInfo.lpAddress).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );

        uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeBalance;
        _toMasterWomAndSendReward(_lpAddress, lpReceived, true); // triggers harvest from wombat exchange
        // update variables
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
        emit NewDeposit(_for, depositToken, _amount, poolInfo.receiptToken, lpReceived);
    }
```

**File:** wombat/WombatStaking.sol (L272-287)
```text
    function depositLP(
        address _lpAddress,
        uint256 _lpAmount,
        address _for
    ) nonReentrant whenNotPaused _onlyActivePoolHelper(_lpAddress) external {
        // Get information of the Pool of the token
        Pool storage poolInfo = pools[_lpAddress];

        // Transfer lp to this contract and stake it to wombat
        IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);

        _toMasterWomAndSendReward(_lpAddress, _lpAmount, true); // triggers harvest from wombat exchange
        IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);

        emit NewLPDeposit(_for, poolInfo.lpAddress, _lpAmount, poolInfo.receiptToken, _lpAmount);
    }
```

**File:** wombat/WombatStaking.sol (L295-321)
```text
    function withdraw(
        address _lpToken,
        uint256 _liquidity,
        uint256 _minAmount,
        address _sender
    ) nonReentrant whenNotPaused _onlyPoolHelper(_lpToken) external {
        Pool storage poolInfo = pools[_lpToken];

        IERC20(poolInfo.lpAddress).safeApprove(poolInfo.depositTarget, _liquidity);
        _toMasterWomAndSendReward(_lpToken, _liquidity, false);

        uint256 beforeWithdraw = IERC20(poolInfo.depositToken).balanceOf(address(this));
        IWombatPool(poolInfo.depositTarget).withdraw(
            poolInfo.depositToken,
            _liquidity,
            _minAmount,
            address(this),
            block.timestamp
        );

        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );

        emit NewWithdraw(_sender, poolInfo.depositToken, _liquidity);
    }
```

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

**File:** wombat/WombatStaking.sol (L671-696)
```text
    function _toMasterWomAndSendReward(address _lpToken, uint256 lpAmount, bool _isStake) internal {
        Pool storage poolInfo = pools[_lpToken];

        address[] memory bonusTokens = assetToBonusRewards[_lpToken];
        uint256 bonusTokensLength = bonusTokens.length;

        uint256 womBeforeBalance = IERC20(wom).balanceOf(address(this));
        uint256[] memory beforeBalances = _rewardBeforeBalances(_lpToken);

        if(_isStake)
            _stakeToWombatMaster(_lpToken, lpAmount); // triggers harvest from wombat exchange
        else
            IMasterWombat(masterWombat).withdraw(poolInfo.pid, lpAmount); // triggers harvest from wombat exchange
        uint256 womRewards = IERC20(wom).balanceOf(address(this)) - womBeforeBalance;
        _sendRewards(_lpToken, wom, poolInfo.rewarder, womRewards);

        for (uint256 i; i < bonusTokensLength; i++) {
            uint256 bonusBalanceDiff = IERC20(bonusTokens[i]).balanceOf(address(this)) - beforeBalances[i];
            if (bonusBalanceDiff > 0) {
                _sendRewards(_lpToken, bonusTokens[i], poolInfo.rewarder, bonusBalanceDiff);
            }
        }

        emit WomHarvested(womRewards);

    }
```

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

**File:** wombat/SmartWomConvert.sol (L175-207)
```text
    function _convertFor(uint256 _amount, uint256 _convertRatio, uint256 _minRec, address _for, uint256 _mode)
        internal returns (uint256 obtainedmWomAmount) {

        if (_convertRatio > DENOMINATOR)
            revert IncorrectRatio();

        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
        uint256 convertAmount = _amount - buybackAmount;
        uint256 amountRec = 0;

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

        obtainedmWomAmount = convertAmount + amountRec;
```
