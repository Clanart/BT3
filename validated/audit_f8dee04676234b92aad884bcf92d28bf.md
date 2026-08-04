## Finding: Missing contract-existence check before `.call()` in `dispatchIncoming(GetResponse)` and `dispatchTimeOut(GetRequestTimeout)` lets Hyperbridge treat undelivered messages as permanently handled

### Title
Low-level `.call()` to a non-contract module address is trivially treated as "success", permanently finalizing GET responses/timeouts without executing app logic - (File: evm/src/core/EvmHost.sol)

### Summary
The external report's core broken invariant is: **a low-level external call's "success" signal is trusted without confirming that real code actually executed**, because calling a non-contract address always returns `success = true` with zero return data — the exact "no bytes returned: assume success" branch in the reported `checkSuccess()`. `EvmHost.sol` contains this exact pattern inconsistently. `dispatchIncoming(PostRequest, ...)` explicitly guards against it with an `extcodesize` check before calling the destination, but the sibling functions `dispatchIncoming(GetResponse, ...)` and `dispatchTimeOut(GetRequestTimeout, ...)` do not, even though they follow the identical call-then-check-success pattern.

### Finding Description
`dispatchIncoming(PostRequest memory request, address relayer)` protects against calling an empty address: [1](#0-0) 

Note the `extcodesize(destination)` check at lines 796-803 that early-returns (without writing any receipt) if the destination has no code.

Compare this to `dispatchIncoming(GetResponse memory response, address relayer)`, which has no such check: [2](#0-1) 

Here `_responseReceipts[commitment]` is written **before** the call (line 827-830), then the `.call()` to `_bytesToAddress(response.request.from)` is executed. If that address has no deployed code (e.g., not yet a contract, or self-destructed), the EVM `.call()` opcode returns `success = true` with empty return data — there is nothing to execute, so it cannot fail. The code interprets this as a successful delivery: it keeps the response receipt permanently set (line "replay protection", never deleted) and immediately pays the relayer the fee from `_requestCommitments[commitment].fee` (lines 842-845), even though `onGetResponse` never actually ran on any module.

The same missing guard exists in `dispatchTimeOut(GetRequestTimeout memory timeout, ...)`: [3](#0-2) 

Here the request commitment is deleted for replay protection (line 862) before the call, and if the `from` address has no code the call trivially "succeeds," refunding the relayer's fee (line 872-875) and permanently deleting the commitment — again without any module ever being notified.

Because `HandlerV2.handleGetResponses` / `handleGetRequestTimeouts` enforce a one-time dedup check against `responseReceipts`/`requestCommitments`: [4](#0-3) 

once the false "success" path is taken, the commitment/receipt is finalized and the real GET response/timeout can never be resubmitted or retried — the module's expected callback (e.g., escrow finalization, cancellation refund logic in the intents apps) is silently and permanently dropped, unlike the `PostRequest` path which explicitly returns early (leaving state untouched) so the message can be retried once a real destination exists.

### Impact Explanation
This breaks the "request/response/timeout paths must bind... one-time receipt handling" and "false proof/state acceptance" invariants: the host records irreversible delivery/timeout state and pays relayer fees for a callback that never executed. Any app relying on `onGetResponse`/`onGetTimeout` to release escrow, refund users, or update accounting (e.g., the same/cross-chain cancellation flow in `IntentsBase`/`IntrinsicIntents`/`ExtrinsicIntents`, which calls `_withdraw` from these callbacks) would have funds permanently stuck in escrow with no way to retry, while a relayer is paid a fee for a phantom delivery — a direct fund-loss and false-state-acceptance path reachable by anyone who can get Hyperbridge to route a GET request/response/timeout whose `from`/module address is not (or is no longer) a deployed contract on the destination host.

### Likelihood Explanation
Likelihood is moderate: it requires a GET request's `from` field to resolve to an address with no code at delivery time. This is plausible in practice for GET-based intents flows (e.g., cross-chain `cancelOrder` using `DispatchGet`, where `from`/module addresses are computed/derived rather than hardcoded, or where CREATE2 deployments/proxies are not yet live on all chains) and is a purely permissionless call path (`handleGetResponses`/`handleGetRequestTimeouts` are callable by anyone per the docs: "Access: Permissionless"). No relayer/prover/admin collusion is required — only a normal proof submission against a `from` value that happens to be codeless.

### Recommendation
- Short term: add the same `extcodesize`/code-existence guard used in `dispatchIncoming(PostRequest)` to `dispatchIncoming(GetResponse)` and `dispatchTimeOut(GetRequestTimeout)` (and audit `dispatchTimeOut(PostRequestTimeout)` for the same gap) before treating the `.call()` result as authoritative, and skip/allow-retry rather than finalize-and-pay when the target has no code.
- Long term: centralize the "call target, verify code existed and callback genuinely executed" logic into a single internal helper used by every `dispatchIncoming`/`dispatchTimeOut` variant, so the guard cannot be forgotten when new dispatch paths are added — directly addressing the report's concern that ad hoc, per-call-site success validation is error-prone and order-of-execution sensitive.

### Proof of Concept
1. An application on the destination host dispatches (or a user triggers, e.g. via `cancelOrder`'s `DispatchGet`) a `GetRequest` whose `from` field is set to an address `X` with no deployed code at the time the response/timeout is processed (achievable if `X` is a not-yet-deployed CREATE2 address, or a contract that has since self-destructed).
2. A relayer submits `handleGetResponses` (or `handleGetRequestTimeouts`) with a valid state/non-membership proof for that request.
3. `EvmHost.dispatchIncoming(GetResponse,...)` (or `dispatchTimeOut`) executes `_bytesToAddress(X).call(...)`; since `X` has no code, the call returns `success = true` trivially.
4. The response receipt is kept (or the request commitment deleted) as "handled," the relayer is paid the fee, and `GetRequestHandled`/`GetRequestTimeoutHandled` is emitted — even though no `onGetResponse`/`onGetTimeout` logic ever ran on `X`.
5. Because the commitment/receipt bookkeeping is now finalized, `HandlerV2`'s duplicate-message checks permanently reject any future resubmission, so the intended callback (e.g., escrow release/refund) can never occur — funds remain stuck while the relayer has already been paid for a non-delivery.

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

**File:** evm/src/core/EvmHost.sol (L820-847)
```text
    /**
     * @dev Dispatch an incoming GET response to source module
     * @param response - get response
     */
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

**File:** evm/src/core/HandlerV2.sol (L241-246)
```text
        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // duplicate response?
            if (host.responseReceipts(leaf.response.request.hash()).relayer != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.response, _msgSender());
        }
```
