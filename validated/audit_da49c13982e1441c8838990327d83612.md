## Finding [1](#0-0) 

The `GetResponse` delivery path in `EvmHost.dispatchIncoming(GetResponse,...)` pays the relayer fee out of `_requestCommitments[commitment].fee` but, unlike every other terminal path for a request (`dispatchTimeOut` for both `PostRequestTimeout` and `GetRequestTimeout`, which both explicitly `delete _requestCommitments[commitment]`), it never deletes that entry.### Title
Missing `_requestCommitments` cleanup after GetResponse delivery enables double-payout of relayer fee via a later GetRequestTimeout - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatchIncoming(GetResponse, relayer)` pays the relayer fee from `_requestCommitments[commitment]` but never clears that entry, unlike every other terminal path for a request (`dispatchTimeOut` for both Post and Get timeouts explicitly `delete _requestCommitments[commitment]`). This is the same bug class as the external report: a value that marks "this request has already been finalized" is never reset, so a guard elsewhere (`meta.sender == address(0)` "known request" check in `HandlerV2`) keeps treating the request as still pending, allowing a second, conflicting finalization path (`handleGetRequestTimeouts` → `dispatchTimeOut`) to run against a request that has already been resolved.

### Finding Description
- `EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` (`evm/src/core/EvmHost.sol:824-847`) sets `_responseReceipts[commitment]` and pays the relayer fee out of `_requestCommitments[commitment].fee`, but leaves `_requestCommitments[commitment]` (the `FeeMetadata{sender, fee}`) intact.
- `EvmHost.dispatchTimeOut(GetRequestTimeout, FeeMetadata meta, bytes32 commitment)` (`evm/src/core/EvmHost.sol:856-877`) is the timeout counterpart: it `delete`s `_requestCommitments[commitment]` for "replay protection", invokes `onGetTimeout` on the app, and then refunds `meta.fee` to `meta.sender`.
- `HandlerV2.handleGetRequestTimeouts` (`evm/src/core/HandlerV2.sol:293-321`) gates a timeout purely on: (a) `request.timeout() <= state.timestamp` for some *caller-chosen* previously-finalized `message.height`, (b) `meta.sender != address(0)` in `_requestCommitments` ("known request"), and (c) a non-membership proof of a `ResponseReceipt` *at that same chosen height*. It never checks the EvmHost's own current `_responseReceipts` mapping (contrast with `handleGetResponses`, which does check `host.responseReceipts(...)` for duplicates at `HandlerV2.sol:244`).

Because `_requestCommitments[commitment]` is never cleared by the response path, and because the timeout path can be proven against an arbitrary earlier finalized `state.timestamp ≥ request.timeout()` where the response genuinely did not yet exist, an attacker (any relayer, no privileged role needed) can:
1. Relay the real `GetResponse` via `handleGetResponses` → `dispatchIncoming(GetResponse,...)`, collecting the fee once (legitimate).
2. Separately submit `handleGetRequestTimeouts` with a state-machine height/proof from *before* the response was recorded on Hyperbridge (any height whose timestamp is already past `request.timeout()` — which can occur before the response is actually delivered, since delivery timing is relayer/network dependent). The non-membership proof at that height is genuinely valid (the response wasn't recorded yet at that height), `meta.sender` is still non-zero because it was never deleted, so `dispatchTimeOut` executes: it calls `onGetTimeout` on the destination app (which already received `onGetResponse`) and refunds `meta.fee` a second time to `meta.sender`.

The result: the same escrowed fee is paid out twice (once to the relayer on response, once refunded to `meta.sender` on timeout), and the destination application receives both a fulfillment (`onGetResponse`) and a timeout callback (`onGetTimeout`) for the same request — a state/logic conflict that downstream apps (e.g., escrow/intent-settlement modules relying on exactly-once semantics) are not expected to handle safely.

### Impact Explanation
This falls squarely within the accepted impact categories: loss/duplication of escrowed funds (fee paid out twice from a single escrow) and double-settlement (both `onGetResponse` and `onGetTimeout` fire for the same request commitment on the destination app, which is a false/duplicate execution the app's business logic did not authorize). No malicious relayer/prover/admin assumption is required beyond an unprivileged, permissionless relayer exploiting the ordering — any relayer already has the ability to submit both `handleGetResponses` and `handleGetRequestTimeouts` messages through the standard `Handler`.

### Likelihood Explanation
Medium-to-high: it requires timing (a state commitment height with timestamp past the request's timeout must exist and be provable via non-membership before/without the actual response being recorded at that height), but this is a normal, easily engineered race for a relayer who controls both message submissions and can pick which finalized height to prove against. It does not require a compromised relayer/prover/validator, front-running, or governance — a standard relaying account acting alone is sufficient.

### Recommendation
- In `EvmHost.dispatchIncoming(GetResponse memory response, address relayer)`, `delete _requestCommitments[commitment]` after the fee has been paid (mirroring the cleanup already performed in both `dispatchTimeOut` overloads), so a request can only be finalized once.
- Defense in depth: in `HandlerV2.handleGetRequestTimeouts` (and the Post equivalent), additionally check the host's live `_responseReceipts`/`requestReceipts` state (as `handleGetResponses` already does for duplicates) before allowing a timeout to proceed, rather than relying solely on a non-membership proof at an arbitrarily chosen historical height.
- Add explicit test coverage exercising "response delivered, then timeout submitted for the same commitment" (and vice versa) to ensure only one finalization path can ever succeed per commitment, consistent with the report's broader recommendation to test all control-flow branches around one-time state transitions.

### Proof of Concept
1. Source chain dispatches a `GetRequest` with `fee = F`, `sender = S`; `_requestCommitments[commitment] = {sender: S, fee: F}`.
2. A relayer `R1` submits `handleGetResponses` with a valid membership proof against a finalized Hyperbridge height; `EvmHost.dispatchIncoming(GetResponse,...)` runs: sets `_responseReceipts[commitment]`, transfers fee `F` to `R1`, but leaves `_requestCommitments[commitment] = {S, F}` untouched.
3. Relayer `R2` (can be the same actor) submits `handleGetRequestTimeouts` with `message.height` pointing at an earlier finalized Hyperbridge state whose `state.timestamp >= request.timeout()` but at which no response was yet recorded (a genuinely valid non-membership proof for `ResponseReceipts` at that height).
4. `HandlerV2` checks pass: `meta = host.requestCommitments(commitment)` still returns `{S, F}` (non-zero sender), non-membership proof at the chosen height verifies. `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` executes: deletes `_requestCommitments[commitment]` now, calls `onGetTimeout` on the destination app (already having received `onGetResponse`), and refunds `F` again to `S`.
5. Net effect: `2F` paid out of the host's fee token balance for a single escrowed `F`, and the destination app processed both a response and a timeout for the same request.

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
