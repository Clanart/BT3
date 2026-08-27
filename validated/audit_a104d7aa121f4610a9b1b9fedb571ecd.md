### Title
`WombatPoolHelperV2.depositFor` hardcodes zero minimum liquidity, removing slippage protection for user deposits - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor` hardcodes the `_minimumLiquidity` parameter to `0` when forwarding a deposit to `WombatStaking.deposit`, which in turn forwards it to the Wombat pool's `deposit` function. This removes the slippage-control mechanism that the sibling `deposit`/`depositNative` entry points otherwise expose to callers, mirroring the "ineffective slippage control" bug class described in the external report (missing/insufficient minimum-output enforcement on a deposit that mints liquidity/shares at a manipulable on-chain price).

### Finding Description
`WombatPoolHelperV2` exposes several deposit entry points that all funnel into the internal `_deposit` helper, which forwards a `_minimumLiquidity` value to `WombatStaking.deposit` and ultimately to the underlying Wombat pool's `deposit(token, amount, minimumLiquidity, to, deadline, shouldStake)`: [1](#0-0) 

The public `deposit` and `depositNative` functions correctly accept a caller-supplied `_minimumLiquidity` and pass it through: [2](#0-1) [3](#0-2) 

However, `depositFor` hardcodes the minimum liquidity to `0`, completely disabling slippage protection for that entry point: [4](#0-3) 

This value flows unmodified through `WombatStaking.deposit` into the Wombat pool's liquidity-minting logic, which computes minted liability/liquidity from the current on-chain cash/liability ratio of the asset (the exact "manipulable on-chain price" pattern flagged in the external report): [5](#0-4) [6](#0-5) 

Because `_minimumLiquidity` is forced to `0`, any depositor using `depositFor` accepts *any* amount of minted LP/receipt tokens, no matter how unfavorable. An attacker can sandwich the depositor's transaction by skewing the pool's cash/liability ratio immediately before the deposit executes and reverting it immediately after, capturing the value the depositor should have received as liquidity.

### Impact Explanation
A depositor calling `depositFor` (e.g., a frontend/integrator flow that deposits on behalf of another address, or a user simply using this entry point) can be sandwiched to receive drastically fewer receipt/LP tokens than the fair-price amount, permanently and directly transferring value from the depositor to the attacker. This is a direct theft-of-user-funds scenario reachable from an ordinary, unprivileged wallet — no admin or governance involvement required.

### Likelihood Explanation
Likelihood is moderate-to-high: exploiting it only requires the ability to front-run and back-run a `depositFor` call (e.g., via same-block bundling), which is standard MEV tooling, and swapping token amounts through the Wombat pool to shift its cash/liability ratio. No special privileges are needed, and `depositFor` has no access-control restricting who can call it or for whom.

### Recommendation
Do not hardcode `_minimumLiquidity` to `0` in `depositFor`. Add a `_minimumLiquidity` parameter to `depositFor` (as already exists on `deposit`/`depositNative`) and require callers to supply a caller-computed minimum acceptable liquidity, then forward that real value to `WombatStaking.deposit` instead of `0`.

### Proof of Concept
1. Attacker observes a pending `depositFor(_amount, _for)` transaction in the mempool (or intends to call it themselves for a victim `_for`).
2. Attacker front-runs by swapping into the pool's asset to skew `cash`/`liability` away from equilibrium, which changes the liquidity-minting formula in `exactDepositLiquidityInEquilImpl`-equivalent pool logic in the depositor's disfavor.
3. Victim's `depositFor` executes with `_minimumLiquidity == 0` — this is enforced at the `WombatPoolHelperV2` layer regardless of what the underlying pool would otherwise require, so the deposit succeeds even though the minted receipt tokens are far below fair value: [4](#0-3) 
4. Attacker back-runs by reversing their swap, restoring the pool ratio and realizing profit equal to the value the victim lost in step 3.

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

**File:** mocks/wombat/WombatPoolMock.sol (L36-82)
```text
    function deposit(
        address token,
        uint256 amount,
        uint256 minimumLiquidity,
        address to,
        uint256 deadline,
        bool shouldStake
    ) external returns (uint256 liquidity) {
        IERC20(token).safeTransferFrom(address(msg.sender), address(this), amount);

        IWombatAssetMock lpToken = IWombatAssetMock(depositTokenToLp[token]);

        depositBalance[msg.sender] += amount;

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
        
        if (!shouldStake) {
            lpToken.mint(to, lpTokenToMint);
        } else {
            lpToken.mint(address(this), lpTokenToMint);

            lpToken.approve(address(masterWombat), lpTokenToMint);

            uint256 pid = masterWombat.getAssetPid(address(lpToken)); 
            masterWombat.depositFor(pid, lpTokenToMint, to);
        }

        lpToken.addCash(amount);
        lpToken.addLiability(liabilityToMint);

        return amount;
    }
```
