### Title
Unverified LP token transfer amount in `depositLP` causes receipt-token over-minting and protocol insolvency - ([File: wombat/WombatStaking.sol])

### Summary
`WombatStaking.depositLP()` mints receipt tokens based on the caller-supplied `_lpAmount` parameter instead of the actual balance change of `poolInfo.lpAddress` held by the contract, unlike the sibling `deposit()` function which correctly measures `lpReceived` via a before/after `balanceOf` diff.

### Finding Description
In `WombatStaking.sol`, the `deposit()` function properly guards against any discrepancy between the nominal deposit amount and what the contract actually receives: [1](#0-0) 

It computes `beforeBalance`/`afterBalance` of `poolInfo.lpAddress` and mints the receipt token only for `lpReceived`, the actual balance delta.

However, `depositLP()`, which is reachable directly by an ordinary wallet through any `PoolHelper.depositLP()` (e.g. `WombatPoolHelper.depositLP`, `WombatPoolHelperV2.depositLP`, `AnkrBNBPoolHelper.depositLP`), does not perform this check: [2](#0-1) 

It transfers `_lpAmount` via `safeTransferFrom(_for, address(this), _lpAmount)` and then unconditionally mints `_lpAmount` of `poolInfo.receiptToken` to `msg.sender` (the pool helper), and forwards `_lpAmount` (not an actually-verified received amount) into `_toMasterWomAndSendReward`. There is no `balanceOf` before/after check here, so the minted receipt-token supply is derived from the caller-declared amount, not from what the contract's LP-token balance actually increased by.

This is the same bug class as the WXC exploit: an accounting path that trusts a nominal/declared transfer amount instead of measuring the actual balance change, which breaks down for any LP token whose transfer semantics deduct value (fee-on-transfer, burn-on-transfer, rebasing, or any future token with non-standard `transferFrom` behavior). Because `registerPool()` allows onboarding of new Wombat pools/LP tokens over time, and the LP token address is externally supplied token contract, this contract-level assumption is exactly the same "receipt tokens minted != actual custody" flaw at the root of the WXC report, just at the WombatStaking layer instead of at the WXC token layer.

The downstream pool helpers (e.g. `WombatPoolHelper.depositLP`) correctly measure the *helper's* own receipt-token balance delta before staking to MasterMagpie, but that only protects the helper's local accounting — it does not protect the invariant inside `WombatStaking` between minted receipt-token supply and actual LP-token custody, since the helper still receives and forwards the full minted amount regardless of the true amount `WombatStaking` holds.

### Impact Explanation
If `depositLP` is ever used with an LP token whose `transferFrom` delivers less than `_lpAmount` to `WombatStaking` (fee/burn/rebase mechanics), the receipt-token supply outstrips the actual LP tokens custodied by the contract. Since receipt tokens back withdrawals (`burnReceiptToken` + `IWombatPool.withdraw`), this creates a permanent shortfall: later withdrawers cannot be paid in full, resulting in protocol insolvency and permanent loss of user funds for whichever depositors are left unable to redeem.

### Likelihood Explanation
Triggering requires the configured pool's `lpAddress` to have non-standard transfer semantics (deflationary/fee/burn-on-transfer). This is not guaranteed for the currently registered Wombat AMM LP tokens, but the contract itself provides no defense-in-depth against it, and `registerPool`/`updatePoolHelper` can introduce new LP tokens over the contract's lifetime. Any ordinary user calling the public `depositLP` path is enough to trigger the mismatch once such a token is in use — no privileged action is required to exploit the flaw itself, only to configure the vulnerable token, which is an admin action but the exploitation path from an ordinary wallet is unprivileged and directly reachable.

### Recommendation
Mirror the pattern used in `deposit()`: measure `IERC20(poolInfo.lpAddress).balanceOf(address(this))` before and after `safeTransferFrom` in `depositLP()`, and use the actual received delta for `_toMasterWomAndSendReward` and for the `IMintableERC20(poolInfo.receiptToken).mint(...)` call, instead of trusting the caller-supplied `_lpAmount`.

### Proof of Concept
1. Governance registers a Wombat pool whose `lpAddress` token applies a transfer fee/burn (e.g., 1% burn on transfer) — plausible for any ERC20-compatible LP wrapper token added via `registerPool`.
2. A user calls `PoolHelper.depositLP(100e18)`, which calls `WombatStaking.depositLP(lpToken, 100e18, user)`.
3. `safeTransferFrom(user, WombatStaking, 100e18)` executes, but due to the token's transfer fee, `WombatStaking` only gains `99e18` of actual LP balance.
4. `WombatStaking` still mints `100e18` of `receiptToken` to the pool helper (via `IMintableERC20(...).mint(msg.sender, _lpAmount)` at `wombat/WombatStaking.sol:284`), and forwards `100e18` (not the true `99e18`) into `_toMasterWomAndSendReward`.
5. Total minted receipt-token supply now exceeds the LP tokens actually held/staked by `WombatStaking`, so the pool cannot honor 1:1 withdrawals for all receipt-token holders — the shortfall accumulates with every such deposit, eventually leaving some depositors permanently unable to withdraw their share. [2](#0-1)

### Citations

**File:** wombat/WombatStaking.sol (L252-269)
```text
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
