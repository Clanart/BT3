### Title
Response/timeout delivery to EOA `from` addresses is silently accepted as successful execution - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatchIncoming(GetResponse, relayer)`, `EvmHost.dispatchTimeOut(GetRequestTimeout, ...)`, and `EvmHost.dispatchTimeOut(PostRequestTimeout, ...)` all invoke the destination module's callback with a raw low-level `.call()` and treat `success == true` as proof that the module actually processed the message. Unlike its sibling `dispatchIncoming(PostRequest, relayer)`, none of these three functions first checks `extcodesize` on the target address [1](#0-0)  before dispatching. A low-level `.call()` to an address with no code always returns `success = true` with empty return data, so any codeless (EOA) `from` address makes every response/timeout callback trivially "succeed" without executing any real validation logic — exactly the same broken invariant as the referenced report's `isActive()` call succeeding against an EOA.

### Finding Description
`dispatchIncoming(PostRequest, ...)` guards against calling an address with no code: [1](#0-0) 

But the GetResponse delivery path has no such guard — it unconditionally stores the response receipt, calls `from`, and on the trivially-true success treats the message as fully processed, paying out the relayer fee: [2](#0-1) 

The same pattern repeats for both timeout callbacks, which delete the replay-protection state *before* calling `timeout.request.from` and only restore it if the call fails: [3](#0-2) 

Crucially, `from` is fully attacker-controlled and is set to `_msgSender()` with no requirement that the caller be a contract implementing `IApp`: [4](#0-3) [5](#0-4) 

Any unprivileged EOA can call `dispatch(DispatchPost)`/`dispatch(DispatchGet)` directly, setting `from = msg.sender`. Because the destination-side handlers never verify `from` has code, every relayed response/timeout for that request is guaranteed to be accepted as "successfully handled" — `_responseReceipts`/`_requestCommitments` are finalized and relayer fees are released — with zero actual on-chain verification of the callback logic, since there is no code at `from` to run or revert.

### Impact Explanation
This breaks the "one-time receipt handling" and "false state acceptance" invariants the bounty explicitly calls out: the protocol's commitment/receipt bookkeeping (`_responseReceipts`, `_requestCommitments`) is finalized to a "handled" state purely because a call against a codeless address cannot fail, not because any module logic actually validated or consumed the payload. Any downstream logic (in Hyperbridge itself, or in third-party integrations that read `responseReceipts`/emitted `GetRequestHandled`/`PostRequestTimeoutHandled` events as proof that a module processed a message) can be misled into treating an EOA-originated, never-actually-processed message as legitimately settled. This is a direct code-level match to the external report's core defect: a call to a codeless address is misinterpreted as valid confirmation, permanently altering protocol state.

### Likelihood Explanation
`dispatch(DispatchPost)`/`dispatch(DispatchGet)` are public, unprivileged entry points on `EvmHost` reachable by any EOA with no code — no relayer, prover, admin, or governance role is required to trigger the condition. The missing guard is deterministic (not proof- or timing-dependent) and trivially reproducible.

### Recommendation
Add the same `extcodesize`/code-presence check used in `dispatchIncoming(PostRequest, ...)` to `dispatchIncoming(GetResponse, ...)`, `dispatchTimeOut(GetRequestTimeout, ...)`, and `dispatchTimeOut(PostRequestTimeout, ...)` before invoking the low-level `.call()`, and early-return (preserving the retry-able commitment) when the target has no code, exactly mirroring the PostRequest path's behavior.

### Proof of Concept
1. Attacker (EOA) calls `EvmHost.dispatch(DispatchGet{...})` directly, funding `get.fee`. `from` is stored as `abi.encodePacked(msg.sender)` — the attacker's own EOA [5](#0-4) .
2. A relayer submits any (even minimally valid membership-proof-passing) `GetResponse` for this commitment; the handler proceeds directly to `_bytesToAddress(response.request.from).call(...)` [6](#0-5) .
3. Since `response.request.from` is an EOA, the call returns `success = true` trivially — no `onGetResponse` logic ever executes.
4. `_responseReceipts[commitment]` remains permanently set, `GetRequestHandled` is emitted, and the relayer fee is paid out from `_requestCommitments[commitment].fee` — the message is finalized as fully processed despite zero validation logic having run.

### Citations

**File:** evm/src/core/EvmHost.sol (L794-818)
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

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
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

**File:** evm/src/core/EvmHost.sol (L936-948)
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

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```

**File:** evm/src/core/EvmHost.sol (L988-1001)
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

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
```
