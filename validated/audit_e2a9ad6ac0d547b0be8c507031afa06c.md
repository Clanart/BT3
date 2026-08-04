### Title
Asymmetric `FrozenStatus` gating lets requests be dispatched (locking assets) while their timeout/refund path is blocked - ([File: evm/src/core/EvmHost.sol], [File: evm/src/core/HandlerV2.sol])

### Summary
`EvmHost` and `HandlerV2` implement two different, non-mirrored frozen-state checks. Outgoing dispatch is only blocked when `FrozenStatus` is `Outgoing` or `All`, while all handler-side callback processing (including post-request **timeouts**, which are the refund/unlock path for a previously dispatched request) is blocked when `FrozenStatus` is `Incoming` or `All`. When the host is set to `FrozenStatus.Incoming`, users can still freely call `dispatch()` (burning/locking tokens in apps like `HyperFungibleToken`), but the corresponding `handlePostRequestTimeouts` call that would refund them if the request times out is rejected — exactly mirroring the reported bug class where "supply" stays open while "exit" is disabled during a paused state.

### Finding Description
`EvmHost` defines its own `notFrozen` modifier used to gate the outgoing `dispatch` entrypoints: [1](#0-0) 

This only reverts for `FrozenStatus.Outgoing` or `FrozenStatus.All`. It does **not** revert when `_frozen == FrozenStatus.Incoming`, so `dispatch()` (and by extension `HyperFungibleToken.send()`/`WrappedHyperFungibleToken.send()`, which burn/lock user funds and call `IDispatcher(_host).dispatch(...)`) remains fully callable while the host is frozen for `Incoming`.

`HandlerV2` defines a separate `notFrozen(IHost host)` modifier used to gate all incoming/timeout processing entrypoints: [2](#0-1) 

This reverts for `FrozenStatus.Incoming` or `FrozenStatus.All`. It is applied uniformly to `handlePostRequests`, `handleGetResponses`, `handlePostRequestTimeouts`, and `handleGetRequestTimeouts`: [3](#0-2) 

Because `handlePostRequestTimeouts` shares the same `Incoming`-triggered guard as genuinely incoming message processing, setting `FrozenStatus.Incoming` (a state intended only to pause *new incoming* deliveries) also blocks the *timeout/refund* path for requests that were dispatched from this chain and never got a response. Meanwhile `dispatch()` on this same chain is untouched by `FrozenStatus.Incoming`, so users can keep burning/locking tokens via apps such as `HyperFungibleToken.send()`: [4](#0-3) 

If such a request later times out while the host is still (or becomes) frozen for `Incoming`, the corresponding refund flow — `HandlerV2.handlePostRequestTimeouts` → `host.dispatchTimeOut(...)` → `HyperFungibleToken.onPostRequestTimeout` (which re-mints the burned tokens back to the sender) — cannot execute: [5](#0-4) 

This is the same broken invariant as the reported analog: the "locking" operation (mint/deposit in the original report, dispatch/burn here) is guarded by one pause condition, while the corresponding "release" operation (withdraw NFT/liquidity in the original report, timeout refund here) is guarded by a *different, non-matching* condition, so users can enter a state where funds are provably locked in the protocol with no available exit while the freeze persists.

### Impact Explanation
Users who call `send()`/`dispatch()` while `FrozenStatus.Incoming` is active (or is later set while their request is in flight) have their tokens burned/escrowed with no way to recover them via the timeout path until the admin/handler explicitly unfreezes or moves state back to `None`/`Outgoing`-only. This is a fund-lock condition reachable by an ordinary unprivileged user simply by using the standard `send`/`dispatch` entrypoint during a period the operators intended only to pause *incoming* message delivery — not to suspend refunds for their own previously-sent messages.

### Likelihood Explanation
`FrozenStatus.Incoming` is a documented, expected operational state (e.g., pausing incoming delivery during an incident) reachable via `setFrozenState`, callable by admin or handler: [6](#0-5) 

No malicious relayer, prover, or compromised key is required — any legitimate freeze-to-`Incoming` operational action, combined with ordinary user activity (`send`), triggers the asymmetry. This is a realistic, foreseeable sequence rather than an edge case.

### Recommendation
Align the two `notFrozen` checks so a single `FrozenStatus` value cannot simultaneously permit locking funds and block refunding them. Concretely, `handlePostRequestTimeouts` (and `handleGetRequestTimeouts`) should be gated the same way outgoing `dispatch()` is gated (i.e., only blocked by `Outgoing`/`All`, not `Incoming`), since timeouts refund a request originated on this chain and are conceptually part of the outgoing lifecycle, not new incoming delivery. Alternatively, `dispatch()` should also be blocked by `Incoming` so that no new locking can occur whenever any callback processing (including future timeouts) might be unavailable.

### Proof of Concept
1. Admin/handler calls `EvmHost.setFrozenState(FrozenStatus.Incoming)`.
2. A user calls `HyperFungibleToken.send(params)`; this only checks the app's own `whenNotPaused` (unrelated to host `FrozenStatus`) and calls `IDispatcher(_host).dispatch(...)`, which succeeds because `EvmHost.notFrozen` only blocks `Outgoing`/`All` — tokens are burned and the request commitment is stored.
3. The request times out on the destination chain (never delivered/acknowledged).
4. A relayer submits the timeout proof via `HandlerV2.handlePostRequestTimeouts(host, message)`; this reverts with `HostFrozen()` because `HandlerV2.notFrozen(host)` blocks when `host.frozen() == FrozenStatus.Incoming`.
5. The user's burned tokens cannot be re-minted via `HyperFungibleToken.onPostRequestTimeout` until the admin unfreezes the host, even though the app itself was never explicitly paused.

### Citations

**File:** evm/src/core/EvmHost.sol (L351-357)
```text
    /*
     * @dev Check if outgoing messages are permitted
     */
    modifier notFrozen() {
        if (_frozen == FrozenStatus.Outgoing || _frozen == FrozenStatus.All) revert FrozenHost();
        _;
    }
```

**File:** evm/src/core/EvmHost.sol (L742-753)
```text
    /**
     * @dev set the new state of the bridge
     * @param newState new state
     */
    function setFrozenState(FrozenStatus newState) external {
        address caller = _msgSender();
        if (caller != _hostParams.admin && caller != _hostParams.handler) revert UnauthorizedAction();

        _frozen = newState;

        emit HostFrozen({status: newState});
    }
```

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

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L264-282)
```text
    function send(SendParams calldata params) external payable whenNotPaused {
        _burn(msg.sender, params.amount);
        DispatchPost memory request = _buildDispatchPost(params);

        bytes32 commitment;
        if (msg.value > 0) {
            commitment = IDispatcher(_host).dispatch{value: msg.value}(request);
        } else {
            commitment = dispatchWithFeeToken(request);
        }

        emit Sent({
            from: msg.sender,
            to: params.to,
            dest: string(params.dest),
            amount: params.amount,
            commitment: commitment
        });
    }
```

**File:** sdk/packages/core/contracts/apps/HyperFungibleToken.sol (L314-325)
```text
    /**
     * @notice Handles timeout of a previously dispatched cross-chain transfer
     * @dev Called by the ISMP host when a sent message times out without being delivered.
     * Re-mints the burned tokens back to the original sender as a refund.
     * @param incoming The timed-out POST request and the relayer that submitted the timeout proof
     */
    function onPostRequestTimeout(PostRequestTimeout memory incoming) external override onlyHost whenNotPaused {
        Message memory message = abi.decode(incoming.request.body, (Message));
        address refundee = _toAddr(message.from);
        _mint(refundee, message.amount);
        emit Refunded({to: refundee, amount: message.amount});
    }
```
