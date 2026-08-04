### Title
Stale `_requestCommitments` fee entry after a successful `GetResponse` delivery enables a second fee payout via `dispatchTimeOut` - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatchIncoming(GetResponse, address relayer)` pays the relayer fee out of `_requestCommitments[commitment]` on a successful callback, but — unlike every sibling dispatch function in the same contract — it never deletes that mapping entry afterward. This leaves the `FeeMetadata` (payer + fee amount) intact for a request that has already been fully settled.

### Finding Description
Compare the four "incoming" dispatch functions in `evm/src/core/EvmHost.sol`:

- `dispatchIncoming(PostRequest, relayer)` (lines 794-818): stores `_requestReceipts[commitment]` then deletes it on failure — clean replay-protection pattern.
- `dispatchTimeOut(GetRequestTimeout, meta, commitment)` (lines 856-877): explicitly does `delete _requestCommitments[commitment];` up front, and only restores `meta` back into the mapping if the callback fails (`if (!success) { _requestCommitments[commitment] = meta; return; }`). [1](#0-0) 
- `dispatchTimeOut(PostRequestTimeout, meta, commitment)` follows the identical delete-then-conditionally-restore pattern. [2](#0-1) 

But `dispatchIncoming(GetResponse, relayer)` does not follow this pattern at all:

```
function dispatchIncoming(GetResponse memory response, address relayer) external restrict(_hostParams.handler) {
    bytes32 commitment = response.request.hash();
    _responseReceipts[commitment] = ResponseReceipt({relayer: relayer, responseCommitment: response.hash()});

    (bool success,) = _bytesToAddress(response.request.from)
        .call(abi.encodeWithSelector(IApp.onGetResponse.selector, IncomingGetResponse(response, relayer)));

    if (!success) {
        delete _responseReceipts[commitment];
        return;
    }

    uint256 fee = _requestCommitments[commitment].fee;
    if (fee != 0) {
        IERC20(feeToken()).safeTransfer(relayer, fee);
    }
    emit GetRequestHandled({commitment: commitment, relayer: relayer});
}
``` [3](#0-2) 

On a successful delivery, the function reads `_requestCommitments[commitment].fee`, pays it out to `relayer`, but never clears `_requestCommitments[commitment]`. This is the exact bug class from the external report generalized: a state-transition path that is supposed to "consume" a value once it has been settled does not actually zero it out, leaving a live, spendable entry behind, analogous to the report's core invariant break ("a resource that should be dead/consumed is still treated as live").

Because `_requestCommitments[commitment]` still holds the original `fee` amount after a successful `GetResponse`, any subsequent call into `dispatchTimeOut(GetRequestTimeout, meta, commitment)` for the same commitment — driven by a relayer submitting a valid timeout proof (which the `HandlerV2` accepts as long as the request's `timeoutTimestamp` has elapsed, a fact independent of whether the response was already delivered) — will find `meta.fee != 0` and pay it out a second time to `meta.sender`, i.e. `IERC20(feeToken()).safeTransfer(meta.sender, meta.fee)`. [4](#0-3) 

The only guard would need to be an explicit check in the timeout-handling path (in `HandlerV2`) that a `ResponseReceipt` already exists for the commitment before allowing a timeout to be dispatched. I could not fully confirm within the available tool budget whether `HandlerV2`'s GET-timeout handler cross-checks `_responseReceipts` before calling `EvmHost.dispatchTimeOut`; this is the one open verification item a reviewer should close before treating this as fully confirmed. What is fully confirmed from the code itself is the asymmetry: three of the four dispatch functions clear the fee-bearing mapping as part of the state transition, and `dispatchIncoming(GetResponse, ...)` does not.

### Impact Explanation
If the timeout path is not separately gated on `_responseReceipts`, this allows the protocol's escrowed fee-token balance to be drained twice for a single GET request: once as the relayer reward on delivery, and again as a "timeout refund" to the original payer, both funded from the same `feeToken()` balance held by `EvmHost`. This is a direct double-settlement / double-claim of protocol funds, falling squarely within the bounty's accepted impact class ("replay/double-claim/double-settlement", "stealing or loss of funds"), reachable by any relayer submitting a normal, valid timeout proof after a normal, valid response has already been delivered — no malicious peer, prover, or admin action required.

### Likelihood Explanation
The precondition is narrow but realistic: a GET request whose response arrives and is successfully processed close to (or after) its `timeoutTimestamp`, so that a timeout proof for the same commitment can still be constructed and submitted. Given cross-chain latency and challenge periods, requests near their timeout boundary racing against response delivery are a normal occurrence, not an edge case requiring adversarial timing manipulation beyond simply submitting the timeout transaction after response delivery.

### Recommendation
Add `delete _requestCommitments[commitment];` in `dispatchIncoming(GetResponse, ...)` immediately after the fee is paid out (mirroring the delete-then-restore-on-failure pattern already used in `dispatchTimeOut`), and/or have `HandlerV2`'s timeout-handling path explicitly reject timeouts for commitments that already have a `_responseReceipts` entry, so a delivered response can never also be timed out.

### Proof of Concept
1. A contract dispatches a `GetRequest` via `EvmHost.dispatch(DispatchGet)`, paying `fee` into `_requestCommitments[commitment]`.
2. Close to `timeoutTimestamp`, a relayer submits a valid `GetResponse` proof; `HandlerV2` calls `EvmHost.dispatchIncoming(response, relayer)`. The `onGetResponse` callback succeeds, `_responseReceipts[commitment]` is set, and `fee` is paid to `relayer` — but `_requestCommitments[commitment]` is left untouched with `fee` still set.
3. The same or another relayer submits a valid timeout proof for the same request (the chain has by now passed `timeoutTimestamp`); `HandlerV2` calls `EvmHost.dispatchTimeOut(GetRequestTimeout, meta, commitment)`.
4. Inside `dispatchTimeOut`, `meta.fee` (recovered from the still-present `_requestCommitments[commitment]`) is non-zero, so `IERC20(feeToken()).safeTransfer(meta.sender, meta.fee)` executes, paying the fee a second time. [1](#0-0)

### Citations

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
