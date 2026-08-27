### Title
Unvalidated `_for` recipient address in `WombatPoolHelperV2.depositFor` / `AnkrBNBPoolHelper` permanently locks deposited funds under `address(0)` in `MasterMagpie` - (File: wombat/WombatPoolHelperV2.sol)

### Summary
`WombatPoolHelperV2.depositFor(uint256 _amount, address _for)` and the analogous `AnkrBNBPoolHelper._deposit` path let any wallet deposit tokens on behalf of an arbitrary `_for` address, which is passed all the way through to `MasterMagpie.depositFor` without ever validating that `_for != address(0)`.

### Finding Description
`depositFor` transfers the caller's tokens into the helper, deposits them into Wombat, and then stakes the resulting receipt token in `MasterMagpie` on behalf of `_for`, with no zero-address check anywhere in the call chain: [1](#0-0) [2](#0-1) [3](#0-2) 

`MasterMagpie.depositFor` forwards `_for` straight into `_deposit`, which only checks `_onlyPoolHelper` (i.e., that the caller is a registered helper) — it never checks the beneficiary address: [4](#0-3) [5](#0-4) 

If `_for == address(0)`, the accounting is recorded in `userInfo[_stakingToken][address(0)]`. Since no wallet can ever originate a transaction from `address(0)`, and there is no admin/rescue path that lets anyone withdraw on behalf of `address(0)`, the underlying deposit tokens (already pulled from `msg.sender` and converted/staked into the Wombat pool) become permanently unrecoverable, along with any future MGP/bonus emissions that accrue to that "account." The same pattern occurs in `AnkrBNBPoolHelper._deposit`/`_stake`, which also forwards an uncontrolled `_for` into `IMasterMagpie(masterMagpie).depositFor(...)`.

This is the same bug class as the reported issue (`_setOwner` in `Proxy`, `setWalletAddress` in `Accounts`): a user-facing parameter that represents a critical destination/beneficiary address is never checked against `address(0)`, and some clients/integrators may pass a null/zero value by default, resulting in permanent loss.

### Impact Explanation
Any tokens routed through `depositFor` (or equivalent helper functions accepting a `_for` parameter) with a zero address end up staked to `address(0)` in `MasterMagpie`. Both the principal (LP/receipt tokens) and any subsequently accrued but unclaimed MGP/bonus rewards for that position are permanently frozen with no possible recovery mechanism — this meets the "permanent freezing of funds" / "theft or permanent freezing of unclaimed yield" bar.

### Likelihood Explanation
Reachable directly by an ordinary wallet transaction — no privileged role is required. `depositFor` is a normal external function intended for integrators (e.g., zaps, batch helpers) to deposit on behalf of end users; a bug in a calling contract/UI, a misconfigured integration, or default zero-value parameters from a client library could trigger `_for == address(0)`, matching the "some Ethereum clients may default to sending null parameters" scenario cited in the original report.

### Recommendation
Add an explicit zero-address check on the beneficiary parameter (`_for`) at the entry point of every user-facing `...For` function (`WombatPoolHelperV2.depositFor`, `AnkrBNBPoolHelper` deposit paths, and defensively inside `MasterMagpie._deposit`/`depositFor`), reverting if `_for == address(0)`.

### Proof of Concept
1. Any wallet approves `depositToken` to `WombatPoolHelperV2`.
2. Wallet calls `depositFor(_amount, address(0))`.
3. `_deposit` executes: tokens are pulled from the caller, deposited into the Wombat pool, and `_stake` calls `MasterMagpie.depositFor(stakingToken, _amount, address(0))`.
4. `MasterMagpie._deposit` credits `userInfo[stakingToken][address(0)].amount += _amount` — there is no code path (no wallet, no admin function) that can ever withdraw funds recorded under `address(0)`, permanently freezing the deposited principal and all future yield accrued to that position.

### Citations

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

**File:** wombat/WombatPoolHelperV2.sol (L168-172)
```text
    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _sender) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _sender);
    }
```

**File:** rewards/MasterMagpie.sol (L348-358)
```text
    /// @notice Deposit staking tokens to Master Magpie. Can only be called by pool helper
    /// @param _stakingToken Staking token of the pool
    /// @param _amount Amount to deposit
    /// @param _for Address of the user the pool helper is depositing for, and also harvested reward will be sent to
    function depositFor(
        address _stakingToken,
        uint256 _amount,
        address _for
    ) external whenNotPaused _onlyPoolHelper(_stakingToken) nonReentrant {
        _deposit(_stakingToken, _for, _amount, false);
    }
```

**File:** rewards/MasterMagpie.sol (L482-505)
```text
    function _deposit(address _stakingToken, address _account, uint256 _amount, bool _isVlmgp) internal {
        updatePool(_stakingToken);

        PoolInfo storage pool = tokenToPoolInfo[_stakingToken];
        UserInfo storage user = userInfo[_stakingToken][_account];

        if (user.amount > 0) {
            _harvestMGP(_stakingToken, _account);
        }
        _harvestBaseRewarder(_stakingToken, _account);

        user.amount = user.amount + _amount;
        if (!_isVlmgp) {
            user.available = user.available + _amount;
            IERC20(pool.stakingToken).safeTransferFrom(address(msg.sender), address(this), _amount);
        }
        user.rewardDebt = (user.amount * pool.accMGPPerShare) / 1e12;

        if (_amount > 0)
            if (!_isVlmgp)
                emit Deposit(_account, _stakingToken, _amount);
            else
                emit DepositNotAvailable(_account, _stakingToken, _amount);
    }
```
