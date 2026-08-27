### Title
Hardcoded zero minimum liquidity in `WombatPoolHelperV2.depositFor` removes slippage protection on AMM deposits - (File: `wombat/WombatPoolHelperV2.sol`)

### Summary
`WombatPoolHelperV2.depositFor` hardcodes the `_minimumLiquidity` parameter passed down to `WombatStaking.deposit` (and ultimately to the Wombat AMM's `IWombatPool.deposit`) to `0`, removing the depositor's ability to protect against slippage/frontrunning on the underlying stableswap deposit, mirroring the "missing slippage protection on AMM operations" bug class from the referenced report.

### Finding Description
`depositFor` is a fully public, unprivileged function that pulls `depositToken` from `msg.sender` and deposits it into the Wombat pool on behalf of an arbitrary `_for` address: [1](#0-0) 

Unlike the standard `deposit(uint256 _amount, uint256 _minimumLiquidity)` entrypoint, which lets the caller choose their own slippage tolerance: [2](#0-1) 

`depositFor` calls `_deposit(_amount, 0, _for, address(this))`, hardcoding `_minimumLiquidity` to `0`: [3](#0-2) 

This value flows straight into `WombatStaking.deposit`, which forwards it unchanged to the underlying `IWombatPool.deposit` AMM call, and additionally uses `block.timestamp` as the deadline (no real deadline enforcement): [4](#0-3) 

Wombat pool deposits mint LP based on the pool's `cash`/`liability` curve (asset ratio), so the amount of LP minted for a given deposit is sensitive to the pool's current asset balance — exactly the AMM state that can be manipulated via a sandwich/frontrun, as described in the referenced report for `removeLiquidity`/`swapExactTokensForTokens` with a `0` minimum. Any caller invoking `depositFor` (e.g., a zap/router contract or a user calling directly) has no way to bound the LP amount received, so an attacker watching the mempool can manipulate the Wombat asset's cash/liability ratio immediately before the deposit executes, then reverse it afterward, capturing the difference and reducing the LP minted to the victim.

### Impact Explanation
This results in **direct loss of user funds**: users (or any protocol/zap depositing "for" a user) executing `depositFor` receive fewer receipt/LP tokens than the fair-value deposit would produce, with the difference captured by a frontrunning/sandwiching MEV actor. This matches the accepted impact category of "concrete direct theft of user funds" via slippage manipulation, analogous to the original Cork Protocol finding.

### Likelihood Explanation
`depositFor` is `external` with no access control and reachable by any unprivileged wallet or contract, so the vulnerable path is always exercisable whenever this function is used instead of the properly-protected `deposit`. Because Wombat pools are highly sensitive to cash/liability ratio changes and MEV sandwich bots actively monitor mempools for zero-slippage AMM calls, exploitation is straightforward whenever this specific entrypoint is used.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (instead of hardcoding `0`) so callers can specify their own slippage tolerance, and/or enforce a real deadline instead of `block.timestamp` throughout `WombatStaking.deposit`/`withdraw`.

### Proof of Concept
1. Attacker monitors mempool for a pending `WombatPoolHelperV2.depositFor(amount, for)` call.
2. Attacker frontruns with a large swap/deposit that skews the target Wombat pool's `cash`/`liability` ratio unfavorably for the incoming deposit.
3. Victim's `depositFor` transaction executes with `_minimumLiquidity = 0`, so `IWombatPool.deposit` mints a reduced LP amount reflecting the manipulated ratio — the call cannot revert regardless of how bad the resulting LP amount is.
4. Attacker backruns to restore the pool ratio and/or extract the captured value, having profited from the victim's under-minted LP. [1](#0-0) [4](#0-3)

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

**File:** wombat/WombatStaking.sol (L256-263)
```text
        IWombatPool(poolInfo.depositTarget).deposit(
            depositToken,
            _amount,
            _minimumLiquidity,
            address(this),
            block.timestamp,
            false
        );
```
