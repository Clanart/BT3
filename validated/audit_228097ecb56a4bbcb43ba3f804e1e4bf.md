Confirmed vulnerability. `MasterMagpie.deposit(address _stakingToken, uint256 _amount)` is a fully public, unrestricted function that lets any EOA holding the `stakingToken` (the wombat LP receipt token used by `AnkrBNBPoolHelper`) deposit it directly, transferring `IERC20(pool.stakingToken).safeTransferFrom(msg.sender, address(this), _amount)` and crediting `user.amount`/`user.available` for that same pool key [1](#0-0) , and this uses the exact same `userInfo[_stakingToken][_account]` mapping that `AnkrBNBPoolHelper`'s locked allocation also writes into via `depositFor` [2](#0-1) . `IBaseRewardPool(rewarder).balanceOf` reads from this same `stakingInfo` (i.e. `user.amount`) [3](#0-2) .

### Title
Lock bypass in `AnkrBNBPoolHelper.withdraw` via inflating `balance(msg.sender)` with unrelated `MasterMagpie.deposit` stake - (File: `wombat/AnkrBNBPoolHelper.sol`)

### Summary
`AnkrBNBPoolHelper.withdraw` enforces the 1-year lock by checking that the *remaining* rewarder balance after withdrawal (`rest = this.balance(msg.sender)`) is still ≥ `lockedAmount[msg.sender]`, rather than tracking how much of the locked LP specifically has been withdrawn. Because `balance()` simply reads the shared `MasterMagpie` staking balance for the `stakingToken` pool key, any user can top up that same balance by directly acquiring the LP/`stakingToken` and calling the public, unrestricted `MasterMagpie.deposit()` for the same `stakingToken`, then withdraw the entire locked ankrBNB LP allocation before `unlockTime` while `rest` stays ≥ `lockedAmount`.

### Finding Description
`batchDepositLPFor` (callable only by `ankrOperator`) locks LP for beneficiaries by recording `lockedAmount[_for[i]] += amount` and staking via `IMasterMagpie(masterMagpie).depositFor(stakingToken, amount, _for[i])` [4](#0-3) . Withdrawal is gated by:
```solidity
uint256 rest = this.balance(msg.sender);
if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();
``` [5](#0-4) 
`balance()` forwards to `IBaseRewardPool(rewarder).balanceOf(_address)` [6](#0-5) , which is `vlMGPBaseRewarder`/`BaseRewardPoolV2`-style rewarder reading `MasterMagpie.stakingInfo(stakingToken, _account).amount` [3](#0-2) .

Crucially, `stakingToken` (the wombat LP receipt token) is a generic ERC20 registered as a normal pool in `MasterMagpie` via `add()`, so an unprivileged holder of that token can call the fully public `MasterMagpie.deposit(stakingToken, amount)` directly — no `_onlyPoolHelper` restriction applies to `deposit`, only to `depositFor`/`withdrawFor` [7](#0-6) . This inflates `user.amount`/`user.available` in the exact same `userInfo[stakingToken][attacker]` slot that backs `balance(attacker)`, with no association to the "locked" origin of the funds.

Exploit flow:
1. Attacker is a `batchDepositLPFor` beneficiary with `lockedAmount[attacker] = L`, and `unlockTime` in the future — `balance(attacker) == L`.
2. Attacker acquires `L` (or more) units of `stakingToken` independently (e.g., by depositing LP through `depositLP()`/`deposit()` themselves, or buying/transferring the receipt token) and calls `MasterMagpie.deposit(stakingToken, L)` directly. Now `balance(attacker) == 2L`.
3. Attacker calls `AnkrBNBPoolHelper.withdraw(L, minAmount)`. This calls `IWombatStaking.withdraw` for `L` LP and `_unstake(L, attacker)` → `MasterMagpie.withdrawFor(stakingToken, L, attacker)`, reducing `user.amount` to `L`.
4. `rest = balance(attacker) = L`. The check `lockedAmount[attacker] (L) > rest (L)` is `false`, so it does **not** revert, and the withdrawal (including burning the locked receipt token and returning the underlying wombat LP/ankrBNB to the attacker) completes successfully — despite `unlockTime` not having passed.

The root cause is that the lock check compares against the *aggregate*, fungible rewarder balance instead of tracking whether the specific locked units have been unstaked; any unrelated top-up of the same `stakingToken` pool balance neutralizes the lock entirely.

### Impact Explanation
This breaks the intended 1-year lock invariant for ankrBNB exploit-compensation LP, allowing early exit of otherwise-frozen funds. This matches the Immunefi impact class "permanent freezing of funds" being defeated (i.e., funds that should remain frozen for ≥24 hours/up to 1 year can be withdrawn immediately), and more broadly undermines the compensation-lock mechanism's guarantee for token holders/protocol.

### Likelihood Explanation
Feasible for any beneficiary of `batchDepositLPFor` who can obtain (via their own funds, market purchase, or their own separate `depositLP`) an amount of `stakingToken` at least equal to their locked allocation `L`. No privileged role, reentrancy, or flash loan is even required — a straightforward top-up deposit plus withdraw in two/three transactions suffices, and is fully repeatable for every locked recipient.

### Recommendation
Do not gate the lock check on the aggregate `balance()`. Instead, decrement `lockedAmount[msg.sender]` directly as the locked portion is withdrawn (as the code comment already suggests: "should be corrected as amount so if user extra deposit, can withdraw extra"), e.g. track a separate `lockedRemaining` amount reduced only when actually withdrawing locked units, and revert if `_liquidity` would reduce `lockedRemaining` below zero while `unlockTime > block.timestamp`. Alternatively, isolate locked stakes in a distinct pool/stakingToken key so they cannot be commingled with regular deposits.

### Proof of Concept
Foundry test outline:
1. Deploy `MasterMagpie`, `WombatStaking` (or mock), `AnkrBNBPoolHelper` with `unlockTime = block.timestamp + 365 days`, register `stakingToken` pool with `helper = AnkrBNBPoolHelper`.
2. As `ankrOperator`, call `batchDepositLPFor(lpAmount, [attacker], [100000])` so `lockedAmount[attacker] = L` and `MasterMagpie.userInfo[stakingToken][attacker].amount = L`.
3. As `attacker`, independently obtain `L` units of `stakingToken` (e.g., call `AnkrBNBPoolHelper.depositLP(L)` themselves with separately-owned LP) and then call `MasterMagpie.deposit(stakingToken, L)` directly — assert `rewarder.balanceOf(attacker) == 2L`.
4. As `attacker`, call `AnkrBNBPoolHelper.withdraw(L, 0)` before `unlockTime`.
5. Assert the call **succeeds** (does not revert with `NotAllowed`), `lockedAmount[attacker]` remains unchanged at `L` (stale), and attacker received `L` worth of underlying wombat LP/ankrBNB back — demonstrating the locked allocation was withdrawn early.

### Citations

**File:** rewards/MasterMagpie.sol (L260-266)
```text
    function stakingInfo(address _stakingToken, address _user)
        public
        view
        returns (uint256 stakedAmount, uint256 availableAmount)
    {
        return (userInfo[_stakingToken][_user].amount, userInfo[_stakingToken][_user].available);
    }
```

**File:** rewards/MasterMagpie.sol (L337-358)
```text
    function deposit(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _deposit(_stakingToken, msg.sender, _amount, false);
    }

    /// @notice Withdraw staking tokens from Master Mgapie.
    /// @param _stakingToken Staking token of the pool
    /// @param _amount amount to withdraw
    function withdraw(address _stakingToken, uint256 _amount) external whenNotPaused nonReentrant {
        _withdraw(_stakingToken, msg.sender, _amount, false);
    }

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

**File:** wombat/AnkrBNBPoolHelper.sol (L92-94)
```text
    function balance(address _address) external override view returns (uint256) {
        return IBaseRewardPool(rewarder).balanceOf(_address);
    }
```

**File:** wombat/AnkrBNBPoolHelper.sol (L129-134)
```text
        for (uint256 i = 0; i < _for.length; i++) {
            uint256 amount = lpAmount * _ratios[i] / DENOMINATOR;
            lockedAmount[_for[i]] += amount;
            IMasterMagpie(masterMagpie).depositFor(stakingToken, amount, _for[i]);
            emit NewBatchDeposit(_for[i], amount);
        }
```

**File:** wombat/AnkrBNBPoolHelper.sol (L169-173)
```text
        _unstake(_liquidity, msg.sender);
        uint256 rest = this.balance(msg.sender);
        if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);
```
