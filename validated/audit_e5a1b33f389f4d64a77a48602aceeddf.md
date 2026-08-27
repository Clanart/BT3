## #Vulnerability found

### Title
Hardcoded Zero Slippage Protection in `WombatPoolHelperV2.depositFor` Enables Sandwich Attacks - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor` hardcodes `_minimumLiquidity` to `0` when depositing a user's stablecoins into the underlying Wombat pool via `WombatStaking.deposit`, removing any slippage protection for this deposit path. This mirrors the reported `BondingCurvePool.graduateToken` issue, where an on-chain liquidity-affecting operation is executed with a zero minimum-output guard, making it exploitable via a sandwich attack.

### Finding Description
`depositFor` is a public entry point that anyone can call to deposit `depositToken` on behalf of `_for`: [1](#0-0) 

It calls the internal `_deposit` with a hardcoded `0` for `_minimumLiquidity`: [2](#0-1) 

This flows into `WombatStaking.deposit`, which forwards `_minimumLiquidity` unchecked into `IWombatPool(poolInfo.depositTarget).deposit(...)`: [3](#0-2) 

Unlike the sibling `deposit()` and `depositNative()` functions on the same contract, which accept a caller-supplied `_minimumLiquidity` parameter that lets the user protect themselves against slippage: [4](#0-3) [5](#0-4) 

`depositFor` unconditionally strips this protection. The Wombat pool's `deposit` mints LP tokens based on the pool's cash/liability ratio at execution time (see `exactDepositLiquidityInEquilImpl` usage in the mock deposit logic): [6](#0-5) 

so an attacker who manipulates the pool's cash/liability balance immediately before this deposit (e.g. via a large swap or single-sided deposit/withdraw) and reverses it afterward can cause the deposited stables to mint materially fewer LP tokens than a fair-value deposit would yield, extracting the difference as sandwich profit — analogous to the original report's slippage-less `addLiquidityKAS` call in `BondingCurvePool.graduateToken`.

This function is reachable by ordinary wallets: `ManualCompound.compound()` calls `ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender)` on any registered helper (which `WombatPoolHelperV2` implements), meaning a routine, permissionless reward-compounding transaction from any user is exposed to this zero-slippage deposit: [7](#0-6) 

### Impact Explanation
A sandwich attacker can extract value directly from a depositing/compounding user by manipulating the Wombat pool's cash/liability ratio around the `depositFor` transaction, since there is no floor on the LP tokens minted (`_minimumLiquidity = 0`). This is a direct, concrete loss of user funds (fewer LP/staking tokens minted than fair value), matching the accepted impact class of direct theft of user funds via sandwich attack.

### Likelihood Explanation
`depositFor` is a public, unprivileged function, and it is invoked automatically from the commonly-used `ManualCompound.compound()` flow with no way for the calling user to specify a minimum liquidity, making the exposure both easily reachable and difficult to avoid for callers relying on this compounding path.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (and thread it through to `WombatStaking.deposit`/`IWombatPool.deposit`) so callers (including `ManualCompound`) can specify a slippage-protected minimum LP output, consistent with the existing `deposit()`/`depositNative()` functions on the same contract.

### Proof of Concept
1. Attacker monitors the mempool for a `ManualCompound.compound()` or direct `WombatPoolHelperV2.depositFor()` call.
2. Attacker front-runs it with a transaction that skews the Wombat pool's cash/liability ratio unfavorably for depositors (e.g., a large single-sided swap into the pool).
3. The victim's `depositFor` executes with `_minimumLiquidity = 0`, minting fewer LP tokens than the fair-value amount at pre-attack pricing.
4. Attacker back-runs to restore the pool ratio and pocket the extracted value, exactly as described in the referenced report for `BondingCurvePool.graduateToken`.

### Citations

**File:** wombat/WombatPoolHelperV2.sol (L97-101)
```text
    /// @notice deposit stables in wombat pool, autostake in master magpie    
    /// @param _amount the amount of stables to deposit
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

**File:** wombat/WombatPoolHelperV2.sol (L118-128)
```text
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

**File:** wombat/WombatStaking.sol (L242-269)
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
```

**File:** mocks/wombat/WombatPoolMock.sol (L50-65)
```text
        uint256 liabilityToMint = exactDepositLiquidityInEquilImpl(
            int256(amount),
            int256(uint256(lpToken.cash())),
            int256(uint256(lpToken.liability())),
            int256(ampFactor)
        ).toUint256();

        if (liabilityToMint < amount) {
            liabilityToMint = amount;
        }

        uint256 lpTokenToMint = (
            lpToken.liability() == 0
                ? liabilityToMint
                : (liabilityToMint * lpToken.totalSupply()) / lpToken.liability()
        );
```

**File:** rewards/ManualCompound.sol (L153-155)
```text
                } else if (_helperAddress != address(0)) { 
                    IERC20(_tokenAddress).safeApprove(_helperAddress, receivedBalance);
                    ISimpleHelper(_helperAddress).depositFor(receivedBalance, msg.sender);
```
