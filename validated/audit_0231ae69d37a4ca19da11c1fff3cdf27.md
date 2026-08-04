## Analysis

The core broken invariant in H-6 is: a **pause/freeze switch meant to stop new unsafe activity also blocks the recovery/exit path** (`closeLoan`/`callLiquidation`) for state that was already created, trapping user funds with no way to unwind.

### Local analog in Hyperbridge

`HandlerV2.sol` gates every incoming datagram handler — including timeout handlers — behind the same `notFrozen` modifier: [1](#0-0) 

This modifier is applied identically to `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts`: [2](#0-1) [3](#0-2) [4](#0-3) 

`FrozenStatus.Incoming` is intended to describe "stop accepting new incoming requests/responses from the counterparty chain" (analogous to `pauseCollateralType` stopping new borrows). However, timeout handling (`handlePostRequestTimeouts` / `handleGetRequestTimeouts`) is not "new incoming activity" — it is the **exit path** that lets a user who already dispatched a `PostRequest`/`GetRequest` on this chain recover their locked fee/state once the destination fails to respond in time, exactly as `closeLoan`/`callLiquidation` are the exit path for an already-opened loan. `dispatchTimeOut` (called only from these handlers) is the sole mechanism that deletes the request commitment and returns the escrowed relayer fee/state to the original sender: [5](#0-4) 

Because `handlePostRequestTimeouts`/`handleGetRequestTimeouts` share the exact same `notFrozen` gate as the forward-path handlers, setting `FrozenStatus.Incoming` or `FrozenStatus.All` (a normal, intended admin action for halting a compromised/faulty consensus source — not requiring a malicious relayer or leaked key) simultaneously blocks:
- new incoming request/response delivery (the intended effect), **and**
- all outstanding timeout resolution for requests dispatched by users on this chain before the freeze (an unintended side-effect).

Any `PostRequest`/`GetRequest` fee-escrow, or higher-level app state (e.g. an intent/order awaiting `onPostRequestTimeout` to trigger a refund) tied to a request whose `timeout_timestamp` elapses **while the host is frozen** cannot be unwound: `handlePostRequestTimeouts` reverts with `HostFrozen` until an admin unfreezes the host, and even after unfreezing, if the freeze persists past the point where the counterparty's state proof/receipt window is no longer available, resolution can be delayed indefinitely. Meanwhile the same freeze does nothing to stop the fee/escrow from remaining committed in `requestCommitments`, so user funds and app state are locked with no legitimate way to reclaim them for the freeze duration — mirroring H-6's "outstanding loans cannot be closed... while paused."

### Title
Timeout handlers share the same `notFrozen` incoming-gate as forward-path handlers, blocking fund/state recovery while host is frozen - (File: `evm/src/core/HandlerV2.sol`)

### Summary
`handlePostRequestTimeouts` and `handleGetRequestTimeouts` are gated by the identical `notFrozen(host)` modifier used for `handlePostRequests`/`handleGetResponses`. Freezing the host's `Incoming` (or `All`) status — a normal admin operation to halt processing of new cross-chain messages — also blocks users from resolving already-elapsed timeouts, preventing recovery of escrowed request fees and any app-level state tied to `on_timeout`/`onPostRequestTimeout` callbacks.

### Finding Description
`notFrozen` reverts with `HostFrozen` when `host.frozen()` returns `Incoming` or `All`: [6](#0-5) 
This same check is applied to `handlePostRequestTimeouts` and `handleGetRequestTimeouts`: [3](#0-2) [4](#0-3) 
Yet timeout resolution is fundamentally an exit/settlement path for requests *already dispatched by this chain*, not new inbound traffic — the request commitment and fee metadata already live in `requestCommitments` and are released only via `dispatchTimeOut`: [5](#0-4) 
There is no separate frozen-status carve-out that permits timeout dispatch while blocking only new incoming request/response delivery, unlike the fix applied to the referenced H-6 report where liquidation/closure was decoupled from the pause flag.

### Impact Explanation
While the host remains frozen for `Incoming`/`All` (e.g., during an active consensus incident that legitimately warrants halting new inbound messages), any `PostRequest`/`GetRequest` whose `timeout_timestamp` elapses cannot be timed out. This locks the sender's prepaid relayer fee inside `requestCommitments` and stalls any dependent application logic that relies on `on_timeout`/`onPostRequestTimeout` to release escrowed funds or unwind intent/order state, causing loss/lock of user funds for the freeze duration with no available recovery path — matching the accepted impact class in the analog report.

### Likelihood Explanation
Freezing is a documented, expected admin operation (`setFrozenState`), not a privileged-abuse or malicious-actor scenario, and incidents warranting a freeze (chain outages, consensus faults) are exactly when timeouts are most likely to be actively elapsing. Any request in flight at freeze time is affected, making the condition straightforward to hit during real operational incidents.

### Recommendation
Decouple timeout-handler access from the general `notFrozen(host)` gate the same way H-6 decoupled `closeLoan`/`callLiquidation` from `collateralPaused`. Introduce a distinct check (e.g., only block timeouts when `FrozenStatus.All` truly halts everything, not on `Incoming` alone) or add an explicit exemption so `handlePostRequestTimeouts`/`handleGetRequestTimeouts` remain callable to let users recover fees/state even while new incoming request/response processing is frozen.

### Proof of Concept
1. User calls `dispatch` on `EvmHost` for a `PostRequest` with a short `timeout`, paying a relayer fee that is recorded in `requestCommitments`.
2. Before the destination processes the request, the admin (or handler, per `setFrozenState` permissions) sets `FrozenStatus.Incoming` in response to an unrelated incident.
3. The request's `timeout_timestamp` elapses.
4. A relayer/user calls `handlePostRequestTimeouts` with a valid non-membership proof; the call reverts with `HostFrozen()` due to the `notFrozen(host)` modifier: [7](#0-6) 
5. The fee remains locked in `requestCommitments` and cannot be refunded via `dispatchTimeOut` until the admin unfreezes the host — an unbounded, admin-controlled duration during which user funds are inaccessible.

### Citations

**File:** evm/src/core/HandlerV2.sol (L105-112)
```text
    /**
     * @dev Checks if the host permits incoming datagrams
     */
    modifier notFrozen(IHost host) {
        FrozenStatus state = host.frozen();
        if (state == FrozenStatus.Incoming || state == FrozenStatus.All) revert HostFrozen();
        _;
    }
```

**File:** evm/src/core/HandlerV2.sol (L181-181)
```text
    function handlePostRequests(IHost host, PostRequestMessage calldata request) external notFrozen(host) {
```

**File:** evm/src/core/HandlerV2.sol (L254-260)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();
```

**File:** evm/src/core/HandlerV2.sol (L293-293)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
```

**File:** sdk/packages/core/contracts/interfaces/IHost.sol (L189-205)
```text
    function dispatchTimeOut(
        GetRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external;

    /**
     * @dev Dispatch an incoming post timeout to source app
     * @param timeout - timed-out post request bundled with the relayer that submitted the timeout proof
     * @param meta - fee metadata for the original request
     * @param commitment - request commitment
     */
    function dispatchTimeOut(
        PostRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external;
```
