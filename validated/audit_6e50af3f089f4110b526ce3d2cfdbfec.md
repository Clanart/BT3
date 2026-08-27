### Title
`WombatStaking.depositLP` mints receipt tokens 1:1 with the requested amount instead of the actual LP tokens received, breaking the escrow invariant for non-standard LP tokens - (File: `wombat/WombatStaking.sol`)

### Summary
`WombatStaking.depositLP()` assumes that the amount of LP token pulled via `safeTransferFrom` always equals the `_lpAmount` parameter, and mints receipt tokens and stakes to MasterWombat using that unverified parameter rather than the actually-received balance. This is the same root-cause pattern as the referenced Nibiru finding: a hard assumption of a 1:1 relationship between escrowed tokens and minted accounting tokens, which breaks for any token whose transferred/held balance can differ from the nominal amount (fee-on-transfer, deflationary/rebasing LP tokens).

### Finding Description
In `WombatStaking.sol`, the `deposit()` function correctly measures the actual amount of LP tokens received before minting receipt tokens: [1](#0-0) 

However, `depositLP()`, which is reachable by any regular user through a pool helper, does not perform this check. It transfers `_lpAmount`, then stakes and mints using the raw, unverified parameter: [2](#0-1) 

```
IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);
_toMasterWomAndSendReward(_lpAddress, _lpAmount, true);
IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount);
```

`_toMasterWomAndSendReward` -> `_stakeToWombatMaster` then approves and deposits exactly `_lpAmount` into `IMasterWombat`: [3](#0-2) 

This directly mirrors the flawed assumption from the Nibiru report: "if an asset is created ... a balance ... is left inside the module ... in order to convert back," i.e., an unverified 1:1 relationship between what was requested/escrowed and what is actually held. The entry point for this call is exposed to any wallet via the pool helpers: [4](#0-3) [5](#0-4) 

Both helpers call `wombatStaking.depositLP(lpToken, _lpAmount, msg.sender)` and rely on `WombatStaking` to mint receipt tokens according to what was actually received, but `WombatStaking.depositLP` doesn't do that check unlike its sibling `deposit()` function.

### Impact Explanation
If the pool's `lpAddress` token (the Wombat pool LP/asset token registered via `registerPool`) is ever a token whose transferred amount does not exactly equal the nominal amount passed by the caller (fee-on-transfer behavior, or any balance adjustment tied to transfer), two outcomes are possible:
1. If the actual amount received is less than `_lpAmount`, `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, _lpAmount)` over-mints receipt tokens relative to the LP actually custodied/staked in MasterWombat, and `_stakeToWombatMaster` will attempt to approve/deposit more tokens than `WombatStaking` actually holds, causing insufficient-balance reverts for subsequent stakers/withdrawals, or under-collateralizing every future receipt-token holder's claim.
2. This breaks the accounting invariant between the receipt token total supply (used across `MasterMagpie`/`BaseRewardPool` for staking/reward shares) and actual escrowed LP, leading to protocol insolvency where some receipt-token holders cannot redeem/withdraw their proportional LP, resulting in permanent loss of funds for those users.

This satisfies the "protocol insolvency" / "permanent freezing of funds" impact bar since the mismatch persists in contract state (minted receipt token supply) until a shortfall in the underlying LP token causes withdrawal reverts for later exiting users.

### Likelihood Explanation
The precondition is that a pool is registered via `registerPool()` with an `lpAddress`/depositable LP token that exhibits fee-on-transfer or similar balance-adjusting-on-transfer behavior. This is an unprivileged, ordinary transaction path (any wallet calling `depositLP` on the pool helper); no admin/governance action is required to trigger the bug once such a pool exists — only the initial (owner) pool registration decision determines whether the vulnerable code path is exercised, and the described logic error itself is purely a code defect independent of any malicious actor.

### Recommendation
Mirror the pattern already used in `deposit()`: measure the actual LP balance change via before/after `balanceOf` checks in `depositLP()`, and mint receipt tokens / stake to `MasterWombat` using the actually-received amount rather than the caller-supplied `_lpAmount`:
```solidity
uint256 beforeDeposit = IERC20(poolInfo.lpAddress).balanceOf(address(this));
IERC20(poolInfo.lpAddress).safeTransferFrom(_for, address(this), _lpAmount);
uint256 lpReceived = IERC20(poolInfo.lpAddress).balanceOf(address(this)) - beforeDeposit;

_toMasterWomAndSendReward(_lpAddress, lpReceived, true);
IMintableERC20(poolInfo.receiptToken).mint(msg.sender, lpReceived);
```

### Proof of Concept
1. `WombatStaking.registerPool()` registers a pool where `lpAddress` is a token with fee-on-transfer semantics (e.g., 1% burned/deducted on transfer).
2. A user calls `WombatPoolHelper.depositLP(1000)`, which calls `WombatStaking.depositLP(lpToken, 1000, user)`.
3. `safeTransferFrom(user, address(this), 1000)` only delivers 990 tokens to `WombatStaking` due to the transfer fee.
4. `_stakeToWombatMaster(_lpAddress, 1000)` approves and attempts `IMasterWombat.deposit(pid, 1000)`, pulling 1000 tokens from `WombatStaking`, which only holds 990 — the call reverts with insufficient balance, or (if MasterWombat's underlying asset also charges a fee, netting even less) the accounting further diverges.
5. Even absent an immediate revert (e.g., first deposit into an empty pool with buffer left from previous rounding), `IMintableERC20(poolInfo.receiptToken).mint(msg.sender, 1000)` mints 1000 receipt tokens against only 990 actually escrowed LP, permanently under-collateralizing the receipt token and causing future withdrawals to fail for some holders.

### Citations

**File:** wombat/WombatStaking.sol (L255-269)
```text
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

**File:** wombat/WombatStaking.sol (L708-713)
```text
    function _stakeToWombatMaster(address _lpToken, uint256 _lpAmount) internal {
        Pool storage poolInfo = pools[_lpToken];
        // Approve Transfer to Master Wombat for Staking
        IERC20(_lpToken).safeApprove(masterWombat, _lpAmount);
        IMasterWombat(masterWombat).deposit(poolInfo.pid, _lpAmount);
    }
```

**File:** wombat/WombatPoolHelper.sol (L137-144)
```text


        emit NewWithdraw(msg.sender, _liquidity);
    }

    function harvest() external override {
        IWombatStaking(wombatStaking).harvest(lpToken);
    }
```

**File:** wombat/AnkrBNBPoolHelper.sol (L137-144)
```text
    function depositLP(uint256 _lpAmount) external {
        uint256 beforeDeposit = IERC20(stakingToken).balanceOf(address(this));
        IWombatStaking(wombatStaking).depositLP(lpToken, _lpAmount, msg.sender);
        uint256 afterDeposit = IERC20(stakingToken).balanceOf(address(this));
        _stake(afterDeposit - beforeDeposit, msg.sender);
        
        emit NewLpDeposit(msg.sender, _lpAmount);
    }
```
