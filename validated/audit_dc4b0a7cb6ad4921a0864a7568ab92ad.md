Confirmed only 2 `extcodesize` matches in `EvmHost.sol`, both inside `dispatchIncoming(PostRequest)` — the `GetResponse`/timeout paths have no such check.

### Title
Missing contract-existence check in `dispatchIncoming(GetResponse)`/timeout paths lets responses to non-contract or not-yet-deployed `from` addresses silently "succeed", permanently losing the response and still paying relayer fees - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatchIncoming(PostRequest, address)` explicitly guards against delivering to a destination with no code by checking `extcodesize` and returning early so the batch can be retried later once the app is deployed [1](#0-0) . The sibling functions `dispatchIncoming(GetResponse, address)`, `dispatchTimeOut(GetRequestTimeout, ...)` and `dispatchTimeOut(PostRequestTimeout, ...)` omit this guard entirely and instead call `_bytesToAddress(...).call(...)` directly on the `from` field taken from the original request [2](#0-1) [3](#0-2) [4](#0-3) . Since a low-level `.call` to an address with no code always returns `success = true` in the EVM, these three paths treat "nothing happened" as "the module accepted the response/timeout", finalizing receipts/commitments and paying out relayer fees for a delivery that never actually reached any application logic.

### Finding Description
The `from` field of a `GetRequest`/`PostRequest` is fully attacker-controlled at dispatch time — `dispatch(DispatchGet)` and `dispatch(DispatchPost)` both set `from: abi.encodePacked(_msgSender())` with no restriction that `_msgSender()` be a contract [5](#0-4) [6](#0-5) . Any EOA, or any counterfactually-computed CREATE2 address that has not yet been deployed, can be used as `from`.

When the response/timeout is later delivered:
- `dispatchIncoming(GetResponse)` first commits `_responseReceipts[commitment]` (one-time receipt), then does `_bytesToAddress(response.request.from).call(abi.encodeWithSelector(IApp.onGetResponse.selector, ...))`. If `from` has no code, this `.call` trivially succeeds, so the receipt is never rolled back (the rollback only happens `if (!success)`), and the relayer fee is unconditionally paid out via `IERC20(feeToken()).safeTransfer(relayer, fee)` [2](#0-1) .
- `dispatchTimeOut(GetRequestTimeout)` and `dispatchTimeOut(PostRequestTimeout)` delete the request commitment *before* the call, and only restore it for a retry `if (!success)` [3](#0-2) [4](#0-3) . Because the call to a no-code address always "succeeds," the commitment is deleted for good and (for the Post-timeout path) the escrowed fee is refunded to `meta.sender`, permanently finalizing state for a request whose destination module never actually ran any code.

This is the exact analog of the reported bug class: a delegatecall/call target that is not (yet) a deployed contract is treated as a valid, successful invocation instead of failing, so the protocol silently accepts a "phantom" delivery. The Post-request delivery path (`dispatchIncoming(PostRequest)`) already demonstrates the correct fix pattern (`extcodesize` check + early return so the item can be retried once code exists at the destination), proving this is an inconsistency/regression rather than intended behavior.

### Impact Explanation
- Receipts/commitments are permanently finalized (one-time receipt semantics) even though the destination module never executed any logic, silently and irrecoverably "losing" the cross-chain response/timeout for legitimate integrators who use CREATE2 counterfactual addresses as their `from` module (a common and documented pattern elsewhere in this same codebase, e.g. `BandwidthManager`/host-manager one-shot binding patterns).
- Protocol/escrowed relayer fees are paid out (`dispatchIncoming(GetResponse)`) or forfeited/refunded (`dispatchTimeOut`) based on a no-op call, i.e., funds move on the basis of a fabricated "success" rather than genuine execution — a direct instance of "false state acceptance" driving fund movement to the wrong condition.
- Because the receipt can never be deleted/retried, the message can never be legitimately reprocessed once the module contract *is* deployed, effectively bricking that request/response for the app, unlike the Post-request path which explicitly supports retry.

### Likelihood Explanation
Any unprivileged EOA can trigger this by calling `dispatch(DispatchGet)`/`dispatch(DispatchPost)` directly with `msg.sender` being an EOA or a not-yet-deployed CREATE2 address, no relayer/prover/admin collusion needed. The relayer's role is limited to submitting a normal, valid proof for the response/timeout — an honest relayer following normal flow will trigger this behavior without any malicious intent, so this is not front-run-only nor dependent on a malicious peer.

### Recommendation
Add the same `extcodesize`/contract-existence check used in `dispatchIncoming(PostRequest)` to `dispatchIncoming(GetResponse)`, `dispatchTimeOut(GetRequestTimeout)`, and `dispatchTimeOut(PostRequestTimeout)` before invoking `.call(...)` on the `from` address, returning early (without deleting commitments/paying fees) when no code is present, mirroring the retry semantics already implemented for Post requests.

### Proof of Concept
1. Attacker (EOA `A`, no contract code) calls `EvmHost.dispatch(DispatchGet)` with `get.fee > 0`; `from` is set to `abi.encodePacked(A)` and `_requestCommitments[commitment] = {sender: A, fee: get.fee}`.
2. A relayer delivers a valid GET response via `HandlerV2`, which calls `EvmHost.dispatchIncoming(GetResponse, relayer)`.
3. Inside `dispatchIncoming`, `_responseReceipts[commitment]` is set, then `_bytesToAddress(response.request.from).call(...)` is executed against `A` — since `A` has no code, the call trivially returns `success = true`.
4. Because `success == true`, the receipt is kept (one-time, non-retryable) and `IERC20(feeToken()).safeTransfer(relayer, fee)` executes unconditionally, even though `IApp.onGetResponse` was never actually invoked on any real module.
5. Repeat with `dispatch(DispatchPost)` from EOA `A` and let it time out: `dispatchTimeOut(PostRequestTimeout)` deletes `_requestCommitments[commitment]` and refunds `meta.fee` to `meta.sender`, finalizing state on a no-op call, with no possibility of retry once/if a real contract is later deployed at `A`.

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

**File:** evm/src/core/EvmHost.sol (L856-877)
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
```

**File:** evm/src/core/EvmHost.sol (L885-906)
```text
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

**File:** evm/src/core/EvmHost.sol (L936-944)
```text
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });
```

**File:** evm/src/core/EvmHost.sol (L988-997)
```text
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            timeoutTimestamp: timeoutTimestamp,
            keys: get.keys,
            height: get.height,
            context: get.context
        });
```
