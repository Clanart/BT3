I have enough information now to finalize the analysis.

### Title
Missing slippage protection in `WombatPoolHelperV2.depositFor` exposes depositor/`_for` to sandwich-attack value extraction - (File: `wombat/WombatPoolHelperV2.sol`)

### Summary
`depositFor` hardcodes `_minimumLiquidity = 0` when calling `_deposit(_amount, 0, _for, address(this))`, which is forwarded unchanged to `WombatStaking.deposit` and ultimately to `IWombatPool.deposit`. Because the caller of `depositFor` cannot supply a minimum-liquidity parameter, any front-runner can manipulate the underlying Wombat pool's exchange rate immediately before the deposit executes and profit from the resulting slippage, while `_for` receives fewer receipt/staking tokens than fair value.

### Finding Description
`depositFor` pulls `depositToken` from `msg.sender` and immediately calls `_deposit(_amount, 0, _for, address(this))`: [1](#0-0) 
`_deposit` forwards the hardcoded `0` straight into `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)`: [2](#0-1) 
`WombatStaking.deposit` passes that same `_minimumLiquidity` value unmodified into `IWombatPool(poolInfo.depositTarget).deposit(...)`, the actual AMM call that is subject to exchange-rate slippage: [3](#0-2) 

Unlike the plain `deposit()` entry point, which lets the depositing user choose their own `_minimumLiquidity` value [4](#0-3) , `depositFor` provides no parameter for `_minimumLiquidity` at all — it is unconditionally `0`. This means neither the caller nor `_for` (the beneficiary) has any way to bound acceptable slippage for this call path. The `beforeDeposit`/`afterDeposit` balance-delta pattern in `_deposit` and the `lpReceived` computation in `WombatStaking.deposit` correctly measure and mint exactly what was actually received from the pool — there is no separate "unaccounted for" leftover token trapped anywhere; whatever LP amount is actually returned by the Wombat pool is faithfully minted as receipt/staking token to `_for`. The real defect is that this actual LP amount can be manipulated downward by any unprivileged actor front-running the transaction (a classic sandwich: swap to skew the pool price, let the zero-slippage-protected deposit execute at the bad price, swap back to realize the extracted value), since there is no minimum-liquidity floor to make the transaction revert.

### Impact Explanation
Any pending `depositFor` transaction in the mempool can be sandwiched by an unprivileged MEV actor, extracting value from the pool's AMM curve at the expense of `_for`'s deposit, since the receipt token minted to `_for` is limited by whatever degraded LP amount the manipulated pool returns. This is a direct economic loss (partial loss of principal value) for the deposit beneficiary, falling under "direct theft of user funds" via slippage. It does not require the sandwiching party to also be the `depositFor` caller — it can be any third-party bot watching the mempool, since `_minimumLiquidity=0` removes protection regardless of who calls the function.

### Likelihood Explanation
Exploitation requires only: (1) visibility into pending transactions (public mempool or equivalent), (2) capital to perform a swap that measurably moves the Wombat pool's exchange rate, and (3) the target Wombat pool supporting swap-like slippage on deposit (multi-asset/imbalanced pools such as Wombat's stableswap do exhibit this). No privileged role is needed. This is a repeatable strategy against every `depositFor` call, making it a standing risk rather than a one-off edge case; however, it is a generic sandwich/slippage design gap rather than an accounting bug that permanently strands funds — the loss magnitude depends on pool depth/imbalance at call time.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (and propagate it instead of the hardcoded `0`), allowing callers integrating on behalf of users to specify acceptable slippage bounds, or require depositFor integrations to pass a caller-supplied minimum enforced on-chain before minting the receipt token.

### Proof of Concept
Foundry fork test outline:
1. Fork BNB/Avalanche chain at a block with an active Wombat pool registered in `WombatStaking`.
2. Baseline: call `WombatPoolHelperV2.deposit(amount, minLiquidityFairValue)` as a control user and record `stakingToken` minted.
3. Attack: have a bot address perform a large asset swap against the same Wombat pool to skew its internal coverage ratio, then call `depositFor(amount, victim)` from a second address, then swap back to restore/realize profit.
4. Assert `stakingToken` minted to `victim` in step 3 is materially lower than the baseline fair-value amount from step 2 for the same `amount`, and that the bot's net position (post swap-back) is profitable, demonstrating value extraction enabled by the hardcoded `_minimumLiquidity = 0`.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L99-101)
```text
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender, msg.sender);
    }
```

**File:** wombat/WombatPoolHelperV2.sol (L103-107)
```text
    function depositFor(uint256 _amount, address _for) external {
        IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
        IERC20(depositToken).safeApprove(wombatStaking, _amount);
        _deposit(_amount, 0, _for, address(this));
    }    
```

**File:** wombat/WombatPoolHelperV2.sol (L155-162)
```text
    function _deposit(uint256 _amount, uint256 _minimumLiquidity, address _for, address _from) internal {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, _for);
        
        emit NewDeposit(_for, _amount);
    }
```

**File:** wombat/WombatStaking.sol (L248-269)
```text
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
```
