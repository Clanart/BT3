Confirmed: `depositFor()` in `WombatPoolHelperV2.sol` is `external`, callable by any unprivileged wallet, and hardcodes the `_minimumLiquidity` parameter to `0` when forwarding to `WombatStaking.deposit()`, unlike the sibling `deposit()` function which lets the caller supply their own `_minimumLiquidity`. This directly matches the "missing slippage protection" bug class from the report and is reachable from an ordinary wallet.

### Title
Missing Slippage Protection in `depositFor()` Allows Front-Running Loss of LP Tokens - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor()` lets any unprivileged caller deposit stable tokens into a Wombat pool on behalf of another user, but hardcodes the minimum LP liquidity to `0`, removing any slippage protection for the deposit despite the underlying protocol supporting a caller-specified minimum.

### Finding Description
`WombatPoolHelperV2.deposit()` forwards a user-supplied `_minimumLiquidity` to `WombatStaking.deposit()`: [1](#0-0) 

However, `depositFor()` — which anyone can call to deposit stable tokens for any `_for` address — hardcodes this value to `0`: [2](#0-1) 

That `0` is passed all the way through `_deposit()` into `IWombatStaking(wombatStaking).deposit(lpToken, _amount, _minimumLiquidity, _for, _from)`: [3](#0-2) 

Inside `WombatStaking.deposit()`, `_minimumLiquidity` is passed unchanged as the slippage-protection parameter into the actual Wombat pool `deposit()` call: [4](#0-3) 

Because `_minimumLiquidity` is fixed at `0`, this call accepts any amount of LP tokens returned by the Wombat pool no matter how unfavorable, exactly matching the bug class described in the external report (hardcoded zero minimum-amount parameters in liquidity operations).

### Impact Explanation
An attacker monitoring the mempool can sandwich a `depositFor()` transaction (e.g., by manipulating the Wombat pool's internal exchange rate/coverage ratio just before the deposit and reverting it just after), causing the depositing user to receive significantly fewer LP/staking tokens — and therefore fewer future staking rewards and less redeemable principal — than they should for the stable tokens they supplied. This is a direct, reachable value-extraction vector against ordinary users' deposited funds, not merely a theoretical gas or informational issue.

### Likelihood Explanation
`depositFor()` is `external` with no access control beyond the caller providing/approving the tokens, so any wallet (including automated relayers/bots depositing on behalf of users, or a user's own transaction being front-run) can trigger this path. The absence of slippage protection is unconditional — it does not require any special pool state, admin action, or privileged role, making exploitation straightforward for any actor capable of front-running (i.e., any MEV searcher).

### Recommendation
Add a `_minimumLiquidity` parameter to `depositFor()` so the caller (or the depositing party) can specify an acceptable slippage bound, consistent with `deposit()` and `depositNative()`, instead of hardcoding it to `0`.

### Proof of Concept
1. Attacker observes a pending `depositFor(_amount, _for)` transaction in the mempool targeting a Wombat stable pool via `WombatPoolHelperV2`.
2. Attacker front-runs with a large swap/deposit that shifts the pool's coverage ratio unfavorably for the victim's deposit token.
3. Victim's `depositFor()` executes: `IWombatStaking(wombatStaking).deposit(lpToken, _amount, 0, _for, address(this))` at `wombat/WombatPoolHelperV2.sol#L106` accepts any LP amount since `_minimumLiquidity = 0`, so it succeeds with far less LP minted than expected.
4. Attacker back-runs, reversing their manipulation and pocketing the difference, leaving the victim (`_for`) permanently under-minted on staking/receipt tokens for the funds they deposited. [2](#0-1) [5](#0-4)

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
