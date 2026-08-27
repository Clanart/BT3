### Title
`ArbWomUp.incentiveDeposit` uses raw `IERC20.transfer` for USDT rewards, permanently bricking the incentive/reward payout - (File: `wombat/ArbWomUp.sol`)

### Summary
`ArbWomUp.incentiveDeposit`, an unprivileged, user-callable function, pays out USDT rewards using the raw `IERC20(usdt).transfer(...)` call instead of `safeTransfer`. Because real Tether USDT does not return a `bool` from `transfer`, Solidity's ABI decoding of the expected return value reverts every call, permanently breaking the reward-payout path and freezing the USDT reward pool held by the contract.

### Finding Description
`incentiveDeposit` first deposits the caller's WOM via the safe `_deposit` helper, then attempts to reward the caller with USDT using the unguarded interface call: [1](#0-0) 

Unlike every other transfer in this contract and the rest of the codebase, which consistently use `SafeERC20`'s `safeTransfer`/`safeTransferFrom` (e.g. `_deposit` in the same file), this call uses the raw `IERC20.transfer` interface: [2](#0-1) 

Since `IERC20.transfer` is typed to return a `bool`, Solidity generates code that decodes the return data as a `bool` after the low-level call. Real USDT's `transfer` function does not return any data at all, so this decoding step reverts unconditionally — this is exactly the bug class described in the referenced report (H-06), where non-standard-compliant tokens like USDT break calls made through the strict `IERC20` interface without `SafeERC20`.

The rest of the codebase (`WombatStaking.sol`, `MasterMagpie.sol`, `SmartWomConvert.sol`, pool helpers, etc.) consistently guards against this exact issue by using `safeTransfer`/`safeTransferFrom` via `SafeERC20`, confirming that the raw `.transfer` calls in `ArbWomUp.sol` are an inconsistency/oversight rather than an intentional design choice: [3](#0-2) [4](#0-3) 

### Impact Explanation
`incentiveDeposit` is the sole external mechanism for distributing the USDT incentive reward tied to the WOM-to-Arbitrum airdrop deposit flow. Because the USDT `transfer` call at line 76 always reverts against real (non-standard-compliant) USDT, every call to `incentiveDeposit` reverts atomically — no user can ever deposit WOM through this reward path, and the USDT balance held by the contract for rewards becomes permanently undistributable through the intended mechanism. This constitutes a permanent freeze of the reward funds allocated to this airdrop/incentive contract, since there is no unprivileged way to trigger a successful payout once USDT is the configured reward token.

### Likelihood Explanation
Likelihood is high: the bug triggers deterministically and unconditionally whenever `usdt` is set to an actual non-standard-compliant token like Tether USDT, and `incentiveDeposit` is a normal user-facing entry point requiring no special privileges to call.

### Recommendation
Replace the raw `IERC20(usdt).transfer(msg.sender, rewardToSend);` call in `incentiveDeposit` with `IERC20(usdt).safeTransfer(msg.sender, rewardToSend);` using OpenZeppelin's `SafeERC20`, consistent with the rest of the contract and codebase.

### Proof of Concept
1. Deploy `ArbWomUp` with `usdt` set to the real Tether USDT contract address (a non-standard-compliant ERC20 that returns no data from `transfer`).
2. An unprivileged user calls `incentiveDeposit(_amount)` with a nonzero `_amount` after approving WOM.
3. `_deposit` succeeds, transferring WOM in via `safeTransferFrom`.
4. Execution reaches `IERC20(usdt).transfer(msg.sender, rewardToSend);` at [5](#0-4) ; Solidity attempts to ABI-decode a `bool` from USDT's empty return data and reverts.
5. The entire transaction reverts, and this failure is permanent and unconditional for any nonzero `rewardToSend` — the incentive reward mechanism can never succeed while `usdt` points to a non-compliant token.

### Citations

**File:** wombat/ArbWomUp.sol (L69-78)
```text
    function incentiveDeposit(
        uint256 _amount
    ) external _checkAmount(_amount) whenNotPaused nonReentrant {
        uint256 rewardToSend = this.getRewardAmount(_amount, msg.sender);
        _deposit(_amount);
        claimedReward[msg.sender] += rewardToSend;
        
        IERC20(usdt).transfer(msg.sender, rewardToSend);
        emit USDTRewarded(msg.sender, rewardToSend);
    }
```

**File:** wombat/ArbWomUp.sol (L119-125)
```text
    function _deposit(uint256 _amount) internal whenNotPaused {
        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        userWOMDeposited[msg.sender] += _amount;
        totalAccumulated += _amount;

        emit WomDeposited(msg.sender, _amount);
    }
```

**File:** wombat/WombatStaking.sol (L315-318)
```text
        IERC20(poolInfo.depositToken).safeTransfer(
            _sender,
            IERC20(poolInfo.depositToken).balanceOf(address(this)) - beforeWithdraw
        );
```

**File:** wombat/SmartWomConvert.sol (L181-182)
```text
        IERC20(wom).safeTransferFrom(msg.sender, address(this), _amount);
        uint256 buybackAmount = _amount - (_amount * _convertRatio / DENOMINATOR);
```
