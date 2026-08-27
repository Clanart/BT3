### Title
`WombatPoolHelperV2.depositFor` hardcodes `_minimumLiquidity` to `0`, enabling sandwich-attack value extraction from the depositor - ([File: wombat/WombatPoolHelperV2.sol])

### Summary
`WombatPoolHelperV2.depositFor` pulls `_amount` of `depositToken` from `msg.sender` and forwards it to `WombatStaking.deposit` with `_minimumLiquidity` hardcoded to `0`, removing any slippage protection that exists in the sibling `deposit()` function where the caller supplies `_minimumLiquidity` themselves. Because the resulting LP/receipt amount is entirely dependent on the live Wombat pool coverage ratio at execution time, an unprivileged attacker can front-run/back-run (sandwich) the `depositFor` call by skewing the pool ratio just before it executes and restoring it just after, forcing the depositor's `_amount` to convert into materially fewer LP tokens than fair value while the attacker captures the difference.

### Finding Description
`depositFor` is:
```solidity
function depositFor(uint256 _amount, address _for) external {
    IERC20(depositToken).safeTransferFrom(msg.sender, address(this), _amount);
    IERC20(depositToken).safeApprove(wombatStaking, _amount);
    _deposit(_amount, 0, _for, address(this));
}
``` [1](#0-0) 

This flows into `_deposit`, which calls `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)` with `_minimumLiquidity == 0` fixed [2](#0-1) , and ultimately `WombatStaking.deposit` passes that same `0` straight into `IWombatPool(poolInfo.depositTarget).deposit(...)` [3](#0-2) .

Compare this to the ordinary `deposit(uint256 _amount, uint256 _minimumLiquidity)` entry point, where the caller explicitly supplies `_minimumLiquidity` and can protect themselves against unfavorable execution [4](#0-3) . `depositFor` strips this protection entirely regardless of who calls it.

The amount of `lpReceived` staked to `_for` is exactly the wombat-pool-returned LP amount (`afterDeposit - beforeDeposit`) [2](#0-1) , so there's no separate accounting bug — the credited stake correctly matches what the pool paid out. The vulnerability is that what the pool pays out can be manipulated: an attacker can flash-loan-swap into the underlying Wombat pool to skew the token coverage ratio immediately before the depositFor transaction executes (causing the pool's bonding-curve slippage function to mint far fewer LP tokens for the same deposit amount), then reverse the swap immediately after to restore the ratio and collect the swap-fee/slippage spread as profit. None of `nonReentrant`, `whenNotPaused`, or `_onlyActivePoolHelper` on `WombatStaking.deposit` mitigate this, since these guard against reentrancy/pausing/unauthorized callers, not price-impact/slippage.

### Impact Explanation
Whoever's tokens are pulled by `depositFor` (an EOA, or more realistically an integrating router/vault contract that calls `depositFor` on behalf of its own users) receives fewer receipt/LP-backed stake units than a fair-price deposit would yield, for the exact same amount of `depositToken` spent. This is a direct, quantifiable loss of principal value to the depositor, extracted by the attacker via the created slippage — matching the Immunefi "theft of user funds via sandwich due to missing slippage control" impact class.

### Likelihood Explanation
Any depositFor call is visible in the mempool and sandwichable by any unprivileged actor with capital to skew the target Wombat pool's coverage ratio (flash-loanable on most chains Wombat is deployed on, e.g. BNB Chain/Ethereum). No special privileges are required — only capital and standard MEV tooling. This is repeatable on every `depositFor` call, and its magnitude scales with the depositor's size relative to pool depth and how badly the ratio can be skewed within pool caps.

### Recommendation
Add a `_minimumLiquidity` (or equivalent minAmountOut) parameter to `depositFor` that the caller can specify and forward it (instead of the hardcoded `0`) down to `WombatStaking.deposit`/`IWombatPool.deposit`, mirroring the protection already present in `deposit()`.

### Proof of Concept
Foundry fork test against a live Wombat pool used by `WombatPoolHelperV2`:
1. Fork mainnet/BNB at a block with an active Wombat pool tracked by `WombatStaking`.
2. Record fair-price expected `lpReceived` for a benign `depositFor(_amount, victim)` call in isolation (no attacker activity).
3. Simulate attacker: flash-loan a large amount of one pool asset, swap into the Wombat pool to skew its coverage ratio, then in the same block call `depositFor(_amount, victim)` from the victim/integrator, then have the attacker reverse-swap back and repay the flash loan.
4. Assert: `lpReceived` minted/staked to `victim` in step 3 is materially less than the fair-price baseline from step 2, and the attacker's net token balance after repaying the flash loan is positive (profit), demonstrating value extraction enabled by the hardcoded `_minimumLiquidity = 0`.

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

**File:** wombat/WombatStaking.sol (L248-266)
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
```
