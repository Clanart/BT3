### Title
Missing zero-address check in `lockFor` permanently burns locked position funds - ([File: VLMGP.sol])

### Summary
`VLMGP.lockFor` and the equivalent `mWomSV.lockFor` are public/external functions callable by any unprivileged wallet that pull tokens from `msg.sender` and credit a lock position to an arbitrary `_for` address, with no validation that `_for != address(0)`. This mirrors the reported `transferPosition` bug class: a state-mutating, unprivileged function that assigns value/ownership to a caller-supplied address without a zero-address guard.

### Finding Description
`lockFor` takes MGP (or mWom) from `msg.sender` via `_lock(msg.sender, _for, _amount)` and records the locked balance under `_for`: [1](#0-0) 
The equivalent function exists in `mWomSV.sol`: [2](#0-1) 
Neither function checks `_for != address(0)` before calling the internal `_lock` routine. All position-management functions that operate on a locked balance (`startUnlock`, `unlock`, `cancelUnlock`, `forceUnLock`) are keyed on `msg.sender`, e.g.: [3](#0-2) 
If `lockFor` is called with `_for = address(0)` (by error, by a misconfigured integrating contract such as `ArbWomUp3` or `SmartWomConvert`, both of which call `lockFor`/`ILocker(...).lockFor` with a user-controlled `_account`), the deposited tokens are pulled into the contract and the locked balance is recorded against `address(0)`. Since `address(0)` can never be `msg.sender`, that locked position (and thus the underlying MGP/mWom) becomes permanently unrecoverable — no `unlock` or `startUnlock` call can ever reach it.

### Impact Explanation
This results in permanent freezing/loss of user funds (the locked MGP or mWom position and any accrued rewards tied to it), which meets the "permanent freezing of funds" bar. Any ordinary wallet interacting directly with `lockFor`, or indirectly through integrator contracts like `ArbWomUp3._deposit` / `SmartWomConvert.convert` that forward a caller-supplied `_account`/`_for` parameter, can trigger this loss. [4](#0-3) 

### Likelihood Explanation
Requires an unprivileged caller (or a bug in an upstream integrator) to pass `address(0)` as `_for`/`_account`. This is a plausible user/integration error scenario, directly analogous to the `transferPosition(..., address(0))` case in the reference finding — no privileged role is needed to trigger it.

### Recommendation
Add a zero-address check (`if (_for == address(0)) revert InvalidAddress();`) at the start of `lockFor` in both `VLMGP.sol` and `wombat/mWomSV.sol`, and similarly validate any other public `...For(..., address _for/_account)` functions that assign value/positions to a caller-supplied address (e.g., `depositFor` variants in `SimplePoolHelper.sol`, `WombatPoolHelperV2.sol`, `MasterMagpie.sol`).

### Proof of Concept
1. Attacker (or a buggy caller/integration) calls `VLMGP.lockFor(amount, address(0))`.
2. `_lock(msg.sender, address(0), amount)` executes: MGP is transferred from `msg.sender` into the contract, and the lock record is created under `userUnlockings[address(0)]` / equivalent internal locked-balance mapping for `address(0)`.
3. No account can ever call `unlock`, `startUnlock`, or `cancelUnlock` as `address(0)`, so the locked MGP is permanently stuck in the contract, unreachable by anyone including the original depositor.

Note: I could not directly view the body of the internal `_lock` function (only its call sites), so the exact storage layout for the address(0) lock record is inferred from the pattern of `userUnlockings[msg.sender]` used elsewhere in the same contract; the zero-address check is absent in every code path I could inspect leading into `_lock`.

### Citations

**File:** VLMGP.sol (L260-268)
```text
    // @notice lock MGP in the contract
    // @param _amount the amount of MGP to lock
    // @param _for the address to lcock for
    // @dev the tokens will be taken from msg.sender
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```

**File:** VLMGP.sol (L315-323)
```text
    function unlock(uint256 _slotIndex) external override whenNotPaused nonReentrant {
        _checkIdexInBoundary(msg.sender, _slotIndex);
        UserUnlocking storage slot = userUnlockings[msg.sender][_slotIndex];

        if (slot.endTime > block.timestamp)
            revert StillInCoolDown();

        if (slot.amountInCoolDown == 0)
            revert UnlockedAlready();
```

**File:** wombat/mWomSV.sol (L232-240)
```text
    // @notice lock mWom in the contract
    // @param _amount the amount of mWom to lock
    // @param _for the address to lcock for
    // @dev the tokens will be taken from msg.sender
    function lockFor(uint256 _amount, address _for) override external whenNotPaused nonReentrant {
        _lock(msg.sender, _for, _amount);

        emit NewLock(_for, block.timestamp, _amount);
    }
```

**File:** wombat/ArbWomUp3.sol (L199-203)
```text
            IConverter(smartWomConvert).convert(toSwap, _convertRatio, 0, 0);

            uint256 mWomBal = IERC20(mWom).balanceOf(address(this));
            IERC20(mWom).safeApprove(address(mWomSV), mWomBal);
            ILocker(mWomSV).lockFor(mWomBal, _account);
```
