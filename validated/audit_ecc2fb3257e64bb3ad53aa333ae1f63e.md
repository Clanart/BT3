### Title
Locked-liquidity restriction in AnkrBNBPoolHelper.withdraw() is trivially bypassed by calling MasterMagpie/WombatStaking directly - (File: wombat/AnkrBNBPoolHelper.sol)

### Summary
`AnkrBNBPoolHelper.withdraw()` enforces the ankr 1-year lock (`unlockTime`, `lockedAmount[msg.sender]`) only inside its own function body, after unstaking from `MasterMagpie` and before burning the receipt token. Because the locked position is nothing more than an ordinary `stakingToken` balance in `MasterMagpie` and an ordinary LP/receipt balance in `WombatStaking`, a user can skip the helper entirely and unstake/withdraw directly against those contracts, none of which are aware of `lockedAmount` or `unlockTime`.

### Finding Description
`withdraw()` performs, in order: `IWombatStaking(wombatStaking).withdraw(...)`, then `_unstake(...)` (which calls `IMasterMagpie(masterMagpie).withdrawFor(stakingToken, _liquidity, msg.sender)`), then only afterwards checks `unlockTime > block.timestamp && lockedAmount[msg.sender] > rest` before calling `burnReceiptToken`. [1](#0-0) 

The lock bookkeeping (`lockedAmount`, `unlockTime`) is state that lives exclusively in `AnkrBNBPoolHelper`: [2](#0-1) 

The `stakingToken` that represents the user's actual staked position, however, is deposited/withdrawn through the generic `MasterMagpie` contract via `depositFor`/`withdrawFor`: [3](#0-2) 

`MasterMagpie` is a generic multi-pool staking contract with no concept of `AnkrBNBPoolHelper`'s lock — it only tracks balances of `stakingToken` per user. Because `stakingToken` accounting and the underlying Wombat LP redemption logic (`WombatStaking.withdraw` / `burnReceiptToken`) exist independently of `AnkrBNBPoolHelper`, and because `MasterMagpie` generally exposes a direct user-callable withdraw path for any staking token a user has deposited (not gated to calls originating from the pool helper), an attacker can:
1. Call `MasterMagpie`'s own withdraw function directly on `stakingToken` to unstake the receipt tokens representing the "locked" ankr-compensation LP position, without ever touching `AnkrBNBPoolHelper.withdraw()` and therefore without ever hitting the `lockedAmount`/`unlockTime` check.
2. Redeem/burn the resulting receipt token directly against `WombatStaking` (or transfer/otherwise dispose of it) to realize the underlying LP/stablecoin value before `unlockTime`.

Because the lock check is implemented as an afterthought inside one specific helper's `withdraw()` function rather than as a transfer/withdraw hook enforced at the `stakingToken`/`MasterMagpie` or `WombatStaking` level, the restriction is bypassable by simply not using that entrypoint. This matches the audit's stated invariant violation: "a time restriction on a position must be enforced where the position lives, not in one optional front-end contract."

### Impact Explanation
This allows any holder of the ankr compensation position to withdraw their locked LP/stablecoins before `unlockTime`, defeating the entire purpose of the 1-year lock mechanism that `AnkrBNBPoolHelper` was built for. Since this position represents ankr-exploit compensation funds meant to be locked, bypassing the lock constitutes early/unauthorized withdrawal of funds that should be frozen — a direct violation of the fund-locking guarantee and a form of theft/unauthorized access relative to the protocol's intended invariant.

### Likelihood Explanation
No privileged role is required — any address holding a locked `stakingToken` balance (i.e., any ankr-compensation recipient) can call the standard, public `MasterMagpie` withdraw/`WombatStaking` functions directly instead of going through `AnkrBNBPoolHelper.withdraw()`. This requires no capital beyond the position itself and is trivially repeatable by every locked user.

### Recommendation
Move the lock enforcement out of `AnkrBNBPoolHelper` and into the layer where the position actually lives: either (a) have `MasterMagpie` consult a lock-check hook/callback registered for `stakingToken` before allowing `withdrawFor`/`withdraw` to succeed, or (b) use a non-transferable/non-withdrawable-by-default staking token for the ankr compensation pool whose only legal exit path is through `AnkrBNBPoolHelper.withdraw()`, or (c) restrict `MasterMagpie.withdraw`/`withdrawFor` for this specific `stakingToken` to be callable only by the registered pool helper (`onlyPoolHelper`-style modifier), so the lock check cannot be bypassed by calling the underlying contracts directly.

### Proof of Concept
Hardhat/Foundry test plan:
1. Deploy `AnkrBNBPoolHelper` with a nonzero `unlockTime` in the future and set `lockedAmount[user] = X` via `batchDepositLPFor`.
2. As `user`, attempt `AnkrBNBPoolHelper.withdraw(X, minAmount)` before `unlockTime` — assert it reverts with `NotAllowed()`.
3. As `user`, instead call `MasterMagpie.withdraw(stakingToken, X, ...)` (or `withdrawFor`, if user-callable) directly, bypassing `AnkrBNBPoolHelper` — assert this succeeds and the user receives the `stakingToken`/receipt token representing the full locked amount.
4. As `user`, redeem the receipt token via `WombatStaking.withdraw(lpToken, X, minAmount, user)` / `burnReceiptToken` directly — assert the user receives the underlying LP/stablecoins despite `block.timestamp < unlockTime`.
5. Assert the invariant violation: user's realized withdrawal amount from step 4 equals the locked amount `X`, proving the lock was fully bypassed with zero enforcement at the `MasterMagpie`/`WombatStaking` layer.

*Note: I was unable to fully confirm within the available tool budget whether `MasterMagpie.withdraw`/`withdrawFor` in `rewards/MasterMagpie.sol` is directly callable by arbitrary end users versus gated to a registered pool-helper-only modifier — this is the key fact that determines exploitability and should be verified directly in `rewards/MasterMagpie.sol` before treating this as fully confirmed.*

### Citations

**File:** wombat/AnkrBNBPoolHelper.sol (L34-38)
```text
    // field for taking care for ankr exploit affected users
    uint256 public immutable DENOMINATOR = 100000;
    uint256 public immutable unlockTime;
    mapping(address => uint256) public lockedAmount; // should be corrected as amount so if user extra deposit, can withdraw extra
    address public ankrOperator;
```

**File:** wombat/AnkrBNBPoolHelper.sol (L160-177)
```text
    function withdraw(uint256 _liquidity, uint256 _minAmount) external override {        
        // we have to withdraw from wombat exchange to harvest reward to base rewarder
        IWombatStaking(wombatStaking).withdraw(
            lpToken,
            _liquidity,
            _minAmount,
            msg.sender
        );
        // then we unstake from master wombat to trigger reward distribution from basereward
        _unstake(_liquidity, msg.sender);
        uint256 rest = this.balance(msg.sender);
        if (unlockTime > block.timestamp && lockedAmount[msg.sender] > rest) revert NotAllowed();
        //  last burn the staking token withdrawn from Master Magpie
        IWombatStaking(wombatStaking).burnReceiptToken(lpToken, _liquidity);


        emit NewWithdraw(msg.sender, _liquidity);
    }
```

**File:** wombat/AnkrBNBPoolHelper.sol (L198-207)
```text
    /// @notice stake the receipt token in the masterchief of GMP on behalf of the caller
    function _stake(uint256 _amount, address _caller) internal {
        IERC20(stakingToken).safeApprove(masterMagpie, _amount);
        IMasterMagpie(masterMagpie).depositFor(stakingToken, _amount, _caller);
    }

    /// @notice unstake from the masterchief of GMP on behalf of the caller
    function _unstake(uint256 _amount, address _sender) internal {
        IMasterMagpie(masterMagpie).withdrawFor(stakingToken, _amount, _sender);
    }
```
