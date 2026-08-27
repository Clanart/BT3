I found a concrete unprivileged-wallet-reachable analog: `WombatPoolHelperV2::depositFor` hardcodes the slippage-protection parameter to `0`, unlike every other deposit path in the same file (`deposit`, `depositNative`) which forward a caller-supplied `_minimumLiquidity`.

### Title
`WombatPoolHelperV2::depositFor` hardcodes zero minimum liquidity, exposing depositors to unbounded slippage on Wombat pool deposits - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2::depositFor` is a public, unprivileged entry point that lets any wallet deposit `depositToken` into the underlying Wombat pool on behalf of another address (`_for`). Unlike `deposit()` and `depositNative()` in the same contract, which forward a user-supplied `_minimumLiquidity` value, `depositFor` always passes a hardcoded `0` as the minimum-liquidity/slippage-protection parameter to the internal `_deposit` call, which is then forwarded unchanged to `WombatStaking::deposit` and ultimately to the Wombat pool's `deposit()` call.

### Finding Description
`depositFor` pulls `_amount` of `depositToken` from `msg.sender`, approves `wombatStaking`, and calls `_deposit(_amount, 0, _for, address(this))`: [1](#0-0) 

`_deposit` forwards this `0` value straight through to `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)` with no re-validation: [2](#0-1) 

`WombatStaking::deposit` then uses this value verbatim as `minimumLiquidity` in the call to the real Wombat pool's `deposit()`: [3](#0-2) 

The underlying `IWombatPool::deposit` interface explicitly documents `minimumLiquidity` as the slippage-protection floor for LP tokens minted: [4](#0-3) 

By contrast, the sibling functions `deposit(uint256 _amount, uint256 _minimumLiquidity)` and `depositNative(uint256 _minimumLiquidity)` correctly accept and forward a caller-chosen minimum: [5](#0-4) 

This is the exact bug class in the report: the pool-deposit action (`SmartVaultV4::depositYield` → intermediate DEX/vault interactions) lacked a caller-supplied slippage floor and instead relied on a fixed/insufficient tolerance. Here, `depositFor` goes further and removes slippage protection entirely (accepts any non-zero LP output), so the transaction can never revert on slippage regardless of pool state.

### Impact Explanation
Any wallet calling `depositFor` on behalf of itself or another address receives whatever LP/receipt-token amount the Wombat pool happens to mint at execution time, with no floor. If the underlying Wombat pool's cash/liability ratio has moved unfavorably (e.g., due to a preceding large withdrawal, an asset becoming undercollateralized, or any other pool-side price movement between transaction submission and execution), the depositor can receive an amount of `stakingToken` far below fair value, with the difference effectively captured by other pool participants/LPs. This is a direct loss of user funds on deposit, matching the "up to X% loss on each deposit" bug class from the report, except here the loss is unbounded since minimum liquidity is `0` rather than merely `90%`.

### Likelihood Explanation
`depositFor` is a normal unprivileged, external, non-reentrant-guarded entry point (guarded only by the underlying `WombatStaking::deposit`'s `nonReentrant`/`whenNotPaused`/`_onlyActivePoolHelper` modifiers, none of which check slippage). It requires no special permissions and is reachable in the same manner as `deposit`/`depositNative`, which do provide slippage protection — showing the omission in `depositFor` is a functional gap rather than an intentional restriction. Any caller depositing through this path during periods of pool imbalance, or any transaction that is delayed/reordered relative to a pool-state-changing transaction, will silently accept the loss with no way to protect themselves, since there is no parameter to set a floor.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (mirroring `deposit`/`depositNative`) and forward the caller-supplied value into `_deposit` instead of hardcoding `0`:
```solidity
function depositFor(uint256 _amount, uint256 _minimumLiquidity, address _for) external {
    IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
    IERC20(depositToken).safeApprove(wombatStaking, _amount);
    _deposit(_amount, _minimumLiquidity, _for, address(this));
}
```

### Proof of Concept
1. Attacker/observer monitors the Wombat pool used by `WombatPoolHelperV2` (identified by `lpToken`/`depositToken`).
2. A user calls `depositFor(_amount, _for)` (or a batch/relayer calls it on the user's behalf); this eventually calls `IWombatPool(poolInfo.depositTarget).deposit(depositToken, _amount, 0, address(this), block.timestamp, false)` via `WombatStaking::deposit` [6](#0-5) .
3. Before this transaction is mined, another transaction shifts the pool's cash/liability ratio unfavorably (e.g., a large withdrawal of the same asset from the pool, which is a normal unprivileged pool operation available to any Wombat LP, not requiring privileged access to this codebase).
4. The `depositFor` transaction still succeeds because `minimumLiquidity == 0` can never fail the pool's internal slippage check, and the depositor is minted a reduced amount of LP/receipt tokens relative to fair value, permanently and irreversibly locking in a loss on that deposit.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L97-128)
```text
    /// @notice deposit stables in wombat pool, autostake in master magpie    
    /// @param _amount the amount of stables to deposit
    function deposit(uint256 _amount, uint256 _minimumLiquidity) external override {
        _deposit(_amount, _minimumLiquidity, msg.sender, msg.sender);
    }

    function depositFor(uint256 _amount, address _for) external {
        IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
        IERC20(depositToken).safeApprove(wombatStaking, _amount);
        _deposit(_amount, 0, _for, address(this));
    }    

    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }

    function depositNative(uint256 _minimumLiquidity) external payable {
        if(!isNative) revert NotNativeToken();
        // Dose need to limit the amount must > 0?

        // Swap the BNB to wBNB
        _wrapNative();
        // depsoit wBNB to the pool
        IWNative(depositToken).approve(wombatStaking, msg.value);
        _deposit(msg.value, _minimumLiquidity, msg.sender, address(this));
        IWNative(depositToken).approve(wombatStaking, 0);
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

**File:** wombat/WombatStaking.sol (L242-264)
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

```

**File:** interfaces/wombat/IWombatPool.sol (L17-24)
```text
    function deposit(
        address token,
        uint256 amount,
        uint256 minimumLiquidity,
        address to,
        uint256 deadline,
        bool shouldStake
    ) external returns (uint256 liquidity);
```
