### Title
Missing contract-existence check on `EvmHost.dispatchIncoming(GetResponse)` / `dispatchTimeOut(...)` lets a call to a non-existent destination be recorded as successfully delivered, permanently skipping the app callback and locking escrowed funds - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatchIncoming(PostRequest, ...)` explicitly checks `extcodesize(destination)` before making the low-level `.call()` to the destination module, and returns early (leaving the message retryable) if the destination has no code [1](#0-0) . The three sibling delivery paths — `dispatchIncoming(GetResponse, ...)`, `dispatchTimeOut(GetRequestTimeout, ...)`, and `dispatchTimeOut(PostRequestTimeout, ...)` — perform the exact same style of low-level `.call()` to an address derived from message data, but omit this existence check entirely [2](#0-1) [3](#0-2) . This is the exact bug class from the external report: a low-level `.call()` to an address with no deployed code always returns `success = true` with empty return data, so the Host mistakes "nobody answered" for "the app successfully processed the message."

### Finding Description
In all four functions, the boolean `success` returned by `.call(...)` is used to decide whether the cross-chain message has been finally settled:

- `dispatchIncoming(GetResponse, ...)`: on `success == true`, the already-written `_responseReceipts[commitment]` (replay protection) is kept, the relayer is paid the escrowed fee, and `GetRequestHandled` is emitted [2](#0-1) .
- `dispatchTimeOut(GetRequestTimeout, ...)` and `dispatchTimeOut(PostRequestTimeout, ...)`: `_requestCommitments[commitment]` is deleted up front; on `success == true` the fee is refunded and the corresponding `*TimeoutHandled` event fires; only on `success == false` is the commitment restored for a retry [3](#0-2) .

None of these three functions check `extcodesize` on the target address before calling it, unlike `dispatchIncoming(PostRequest, ...)`, which does [1](#0-0) . Per EVM semantics, a `.call()` targeting an address with zero bytecode (an EOA, a not-yet-deployed counterfactual contract, or a destination that has become codeless) always succeeds trivially — the corrupted value is exactly this `success` boolean, which is `true` even though the intended `IApp.onGetResponse` / `IApp.onPostRequestTimeout` / `IApp.onGetTimeout` callback never executed.

Because these paths treat that trivial success identically to a genuine successful callback, the message is irrevocably marked "handled": the replay-protection receipt is finalized, the retry path (`delete`/re-store then `return`, mirroring the `PostRequest` guard) is never taken, and the relayer/refund side-effects fire — but the application-level state transition the callback was supposed to perform (e.g., refunding or releasing escrow) silently never happens, with no mechanism left to retry it.

### Impact Explanation
Both `ExtrinsicIntents`/`IntentGatewayV2` on EVM rely on `onGetResponse` to release escrowed order inputs back to the user once a GET-response proof shows the order was never filled [4](#0-3) , and rely on `onPostRequestTimeout`/timeout-driven POST messages to move escrowed funds (e.g. `RefundEscrow`) between chains [5](#0-4) . If the module address recorded in the original `GetRequest.from` / `PostRequestTimeout.request.from` no longer has code at delivery time (e.g. the app is behind a proxy that has since been destructed/redeployed, or any other scenario producing a codeless target), `dispatchIncoming(GetResponse)`/`dispatchTimeOut(...)` reports the delivery as successful, permanently finalizes the receipt, and pays out the relayer — while the escrow release/refund logic inside the target contract never runs. The affected user's escrowed assets are then stuck with no remaining code path to trigger the refund, since the Host believes the message was already handled. This matches the required impact class of fund loss/lock caused by false "success" reporting in bridge settlement logic, exactly mirroring the audited Gnosis defect.

### Likelihood Explanation
The vulnerable code paths are reachable by the permissioned handler in the normal course of relaying legitimate GET responses and timeouts (no malicious relayer, prover, or governance action is required — this is a logic gap in the Host's own bookkeeping, triggered whenever the destination happens to be codeless at delivery time). The asymmetry with `dispatchIncoming(PostRequest)`, which already contains the correct `extcodesize` guard, demonstrates the fix pattern was known to the developers but not applied uniformly to the other three call sites, making this a concrete, provable local coding defect rather than a hypothetical or external-dependency issue.

### Recommendation
- **Short term:** Add the same `extcodesize(destination) == 0` early-return guard (used in `dispatchIncoming(PostRequest, ...)`) to `dispatchIncoming(GetResponse, ...)`, `dispatchTimeOut(GetRequestTimeout, ...)`, and `dispatchTimeOut(PostRequestTimeout, ...)` before performing the low-level `.call()`, so a codeless destination leaves the message retryable instead of being falsely marked as handled.
- **Long term:** Centralize the "safe call to module" logic (existence check + call + receipt bookkeeping) into a single internal helper used by all four dispatch paths, so future additions cannot reintroduce this inconsistency.

### Proof of Concept
1. Any account dispatches a GET request via `EvmHost.dispatch(DispatchGet)` (or an app such as `ExtrinsicIntents._cancelFromSource`) whose stored `GetRequest.from` module later becomes codeless at the destination chain before the response is relayed back (e.g. proxy self-destructed/redeployed via a race, or a counterfactual address that hasn't been deployed yet).
2. The relayer submits the valid state proof; `pallet`/handler machinery calls `EvmHost.dispatchIncoming(GetResponse, relayer)`.
3. Inside `dispatchIncoming`, `_bytesToAddress(response.request.from).call(...)` targets the codeless address; per EVM rules this returns `(true, "")` [6](#0-5) .
4. Because `success == true`, the function keeps `_responseReceipts[commitment]`, pays the relayer fee, and emits `GetRequestHandled` [7](#0-6)  — even though the intended `onGetResponse` escrow-refund logic never executed.
5. The user's escrow tied to that commitment is now permanently unreachable: there is no code path left in `EvmHost` to re-deliver this response, since the receipt already reflects "handled."

### Citations

**File:** evm/src/core/EvmHost.sol (L794-803)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }
```

**File:** evm/src/core/EvmHost.sol (L824-847)
```text
    function dispatchIncoming(GetResponse memory response, address relayer) external restrict(_hostParams.handler) {
        // replay protection
        bytes32 commitment = response.request.hash();
        _responseReceipts[commitment] = ResponseReceipt({
            relayer: relayer,
            responseCommitment: response.hash()
        });

        (bool success,) = _bytesToAddress(response.request.from)
            .call(abi.encodeWithSelector(IApp.onGetResponse.selector, IncomingGetResponse(response, relayer)));

        if (!success) {
            // so that it can be retried
            delete _responseReceipts[commitment];
            return;
        }

        // reward the relayer fee
        uint256 fee = _requestCommitments[commitment].fee;
        if (fee != 0) {
            IERC20(feeToken()).safeTransfer(relayer, fee);
        }
        emit GetRequestHandled({commitment: commitment, relayer: relayer});
    }
```

**File:** evm/src/core/EvmHost.sol (L856-906)
```text
    function dispatchTimeOut(
        GetRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onGetTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

        if (meta.fee != 0) {
            // refund relayer fee
            IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
        }
        emit GetRequestTimeoutHandled({commitment: commitment, dest: string(timeout.request.dest)});
    }

    /**
     * @dev Dispatch an incoming POST timeout to the source module
     * @param timeout - timed-out post request bundled with the relayer that submitted the timeout proof
     * @param meta - fee metadata for the original request
     * @param commitment - request commitment
     */
    function dispatchTimeOut(
        PostRequestTimeout memory timeout,
        FeeMetadata memory meta,
        bytes32 commitment
    ) external restrict(_hostParams.handler) {
        // replay protection
        delete _requestCommitments[commitment];
        (bool success,) = _bytesToAddress(timeout.request.from)
            .call(abi.encodeWithSelector(IApp.onPostRequestTimeout.selector, timeout));

        if (!success) {
            // so that it can be retried
            _requestCommitments[commitment] = meta;
            return;
        }

        if (meta.fee != 0) {
            // refund relayer fee
            IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
        }
        emit PostRequestTimeoutHandled({commitment: commitment, dest: string(timeout.request.dest)});
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L269-309)
```text
    /**
     * @dev Handles incoming cross-chain post requests dispatched via Hyperbridge.
     * The first byte of the request body encodes the `RequestKind`, which determines
     * the action to take:
     *
     * - RedeemEscrow: Releases escrowed tokens to the solver who filled the order
     *   on the destination chain. Authenticated against the registered gateway instance.
     * - RefundEscrow: Refunds escrowed tokens to the original user after a successful
     *   cancellation from the destination chain. Authenticated against the registered gateway.
     * - NewDeployment: Registers a new gateway instance for a state machine. Only
     *   Hyperbridge itself may dispatch this request.
     * - UpdateParams: Updates the gateway's configuration parameters and per-destination
     *   protocol fees. Only Hyperbridge may dispatch this request.
     * - SweepDust: Transfers accumulated protocol dust to a specified beneficiary.
     *   Only Hyperbridge may dispatch this request.
     * - UpgradeContract: Points the ERC-1967 proxy at a new implementation, optionally
     *   running migration calldata atomically. Only Hyperbridge may dispatch this request.
     *
     * @param incoming The incoming post request from Hyperbridge.
     */
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost {
        RequestKind kind = RequestKind(uint8(incoming.request.body[0]));
        if (kind == RequestKind.RedeemEscrow || kind == RequestKind.RefundEscrow) {
            _authenticate(incoming.request);
            WithdrawalRequest memory body = abi.decode(incoming.request.body[1:], (WithdrawalRequest));
            return _withdraw(body, kind == RequestKind.RefundEscrow, true);
        }

        // only hyperbridge is permitted to perform these actions
        if (keccak256(incoming.request.source) != keccak256(IDispatcher(host()).hyperbridge())) revert Unauthorized();
        if (kind == RequestKind.NewDeployment) {
            _addDeployment(abi.decode(incoming.request.body[1:], (Deployment)));
        } else if (kind == RequestKind.UpdateParams) {
            _updateParams(abi.decode(incoming.request.body[1:], (ParamsUpdate)));
        } else if (kind == RequestKind.SweepDust) {
            _sweepDust(abi.decode(incoming.request.body[1:], (SweepDust)));
        } else if (kind == RequestKind.UpgradeContract) {
            (address newImpl, bytes memory initData) = abi.decode(incoming.request.body[1:], (address, bytes));
            ERC1967Utils.upgradeToAndCall(newImpl, initData);
        }
    }
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L311-324)
```text
    /**
     * @dev Handles the response to a Hyperbridge GET request dispatched during
     * `_cancelFromSource`. Verifies that the `_filled` storage slot on the destination
     * chain is empty (meaning the order was never filled), then refunds the escrowed
     * tokens to the original user. Reverts with `Filled` if the slot is non-empty.
     *
     * @param incoming The incoming GET response from Hyperbridge containing the storage proof.
     */
    function onGetResponse(IncomingGetResponse calldata incoming) external override onlyHost {
        if (incoming.response.values[0].value.length != 0) revert Filled();

        WithdrawalRequest memory body = abi.decode(incoming.response.request.context, (WithdrawalRequest));
        _withdraw(body, true, true);
    }
```
