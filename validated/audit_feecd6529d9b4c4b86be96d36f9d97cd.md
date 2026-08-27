### Title
Missing slippage protection in `WombatPoolHelperV2.depositFor` allows sandwich-attack value extraction from depositors - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2` exposes two public deposit paths into the same Wombat pool: `deposit(uint256 _amount, uint256 _minimumLiquidity)`, which forwards a caller-supplied minimum-liquidity slippage bound, and `depositFor(uint256 _amount, address _for)`, which hardcodes the minimum liquidity to `0` regardless of caller intent. Any unprivileged wallet calling `depositFor` therefore has no slippage protection on the underlying Wombat pool deposit, unlike a caller who uses `deposit`.

### Finding Description
`deposit` passes the user-supplied `_minimumLiquidity` through to `_deposit`, which calls `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)`: [1](#0-0) 

`depositFor` is a separate, externally callable function with no access-control modifier, and it hardcodes the minimum-liquidity argument to `0`: [2](#0-1) 

`WombatStaking.deposit` uses this value directly as `minimumLiquidity` in the call to the underlying Wombat pool's `deposit`, meaning a `0` value provides no protection against price movement between transaction submission and inclusion: [3](#0-2) 

This is the exact bug class described in the external report (min amount hardcoded to zero, no dynamic slippage protection), but here it manifests as an inconsistency between two sibling entry points into the same underlying deposit logic in the same contract — `deposit` clearly supports a slippage parameter, while `depositFor` silently discards that protection. Because `depositFor` has no `onlyAuthorized`/access-control guard (unlike the analogous `SimplePoolHelper.depositFor`, which is gated by `onlyAuthorized`), any ordinary wallet can invoke it directly and deposit its own funds into the Wombat pool with zero slippage tolerance: [4](#0-3) 

### Impact Explanation
A user (or integrator) calling `depositFor` transfers their own `depositToken` funds into the pool, but because `_minimumLiquidity` is forced to `0`, an attacker can sandwich the deposit transaction (manipulate the Wombat pool's cash/liability ratio immediately before the deposit and restore it after) to minimize the LP/liability minted to the depositor, capturing the difference. This is a direct extraction of value from an ordinary user's transaction — not merely "excess funds swept to a refund address" as in the acknowledged report, since here the depositor's principal is used to mint fewer receipt tokens than a fair-price deposit would yield, and there is no comparable safety net (no refund/sweep mechanism in this pool-helper's deposit flow).

### Likelihood Explanation
`depositFor` is a public, unauthenticated entry point (no modifier restricting the caller), reachable by any address in a single transaction alongside a sandwiching swap against the same Wombat pool. This requires no privileged role and is realistically executable by any MEV-capable actor whenever `depositFor` is used instead of `deposit`.

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor` (mirroring `deposit`) and forward it to `_deposit`/`WombatStaking.deposit` instead of hardcoding `0`, so callers of `depositFor` retain the same slippage protection available via `deposit`.

### Proof of Concept
1. Attacker monitors the mempool for a pending `WombatPoolHelperV2.depositFor(amount, for)` call.
2. Attacker front-runs with a large swap/deposit against the underlying Wombat pool (`poolInfo.depositTarget`) to shift the pool's cash/liability ratio unfavorably for the pending deposit.
3. The victim's `depositFor` executes with `_minimumLiquidity = 0`, so `IWombatPool.deposit` cannot revert even though liquidity minted is far below fair value; `WombatStaking.deposit` mints `lpReceived` (reduced) receipt tokens to the depositor via `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived)`.
4. Attacker back-runs to restore the pool state and captures the value difference, while the victim is left with fewer receipt/staking tokens than a slippage-protected deposit would have produced.

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

**File:** wombat/WombatStaking.sol (L242-263)
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

**File:** wombat/SimplePoolHelper.sol (L45-53)
```text
    function depositFor(uint256 _amount, address _for) external onlyAuthorized {
        IERC20(stakeToken).safeTransferFrom(
            msg.sender,
            address(this),
            _amount
        );
        IERC20(stakeToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakeToken, _amount, _for);
    }
```
