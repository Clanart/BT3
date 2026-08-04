## Summary

The core broken invariant in the Timeswap H‑06 report is: **an external callback fires before all state that finalizes/closes out the operation is updated**, opening a window where a re-entrant or later call can reuse stale state to get double-paid. The closest exact local analog is not in the well-hardened `IntentGatewayV2` fill path (which now sets `_filled[commitment]` at the top of `_fillSameChain`/`_fillCrossChain` before any external call/token transfer — confirmed fixed and regression-tested in `evm/tests/foundry/IntrinsicIntentsReentrancyTest.sol`), but in `EvmHost.dispatchIncoming(GetResponse, ...)`, where the fee-settlement bookkeeping for a fulfilled GET request is never cleared after the external app callback runs and the relayer is paid.

### Title
Fee metadata for fulfilled GET requests is never cleared after `onGetResponse` callback, enabling relayer double-payment via a later timeout proof - (File: evm/src/core/EvmHost.sol)

### Finding Description
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` stores a response receipt for replay protection, then makes an external call into the destination app's `onGetResponse` callback, and only afterward pays the relayer fee out of `_requestCommitments[commitment].fee`: [1](#0-0) 

Unlike the two `dispatchTimeOut` variants in the same contract, which explicitly `delete _requestCommitments[commitment];` immediately as "replay protection" before invoking their respective app callbacks: [2](#0-1) [3](#0-2) 

`dispatchIncoming(GetResponse, ...)` never deletes `_requestCommitments[commitment]` after paying out the fee. The `FeeMetadata` (including the non-zero `.fee`) for that GET request remains live in storage. `HandlerV2.handleGetRequestTimeouts` independently allows a timeout to be processed for the same commitment as long as `host.requestCommitments(commitment).sender != address(0)` (i.e., the entry still exists) and the request's `timeout()` value is `<= state.timestamp` for the proof height supplied: [4](#0-3) 

Because the source-of-truth fee record (`_requestCommitments[commitment]`) is only cleared by the timeout path — and never by the successful-response path — a request whose response has already been delivered and paid can still be timed-out later, since its metadata was never removed. `dispatchTimeOut(GetRequestTimeout, ...)` reads `meta.fee` from this stale entry and pays it out again to whichever relayer submits the timeout proof.

### Impact Explanation
This breaks the "moves exactly once" invariant for relayer reward payouts required by the bounty scope. The same escrowed relayer fee can be released twice: once when `onGetResponse` succeeds and the fee is transferred to the delivering relayer, and again later when a `GetRequestTimeout` proof is submitted and accepted for the same commitment because its `FeeMetadata` was never deleted. This is a direct double-claim / fund-loss primitive against the protocol's fee-token balance, reachable through the public, unprivileged `handleGetRequestTimeouts` entrypoint — no relayer, prover, or admin compromise is required, only a timeout proof for an already-fulfilled request.

### Likelihood Explanation
The precondition is that the original GET request's `timeout()` value be `<= state.timestamp` of some state height that can still be proven after the response was already delivered — which is trivially satisfiable for GET requests dispatched with `timeout = 0` (no expiry), since `0 <= state.timestamp` holds for essentially any subsequently committed height. Any app dispatching GET requests with a zero or already-elapsed timeout (a legitimate, unrestricted choice at dispatch time) creates commitments permanently vulnerable to this double payout, so the likelihood is not merely theoretical.

### Recommendation
Delete `_requestCommitments[commitment]` in `dispatchIncoming(GetResponse, ...)` immediately upon successful delivery (mirroring the pattern already used in both `dispatchTimeOut` overloads), before or immediately after the fee transfer, so that a commitment cannot simultaneously satisfy both the "response delivered" and "eligible for timeout" code paths.

### Proof of Concept
1. App `A` dispatches a `GetRequest` with `timeout = 0` and a non-zero relayer fee; `_requestCommitments[commitment] = {sender: A, fee: F}` is recorded on dispatch.
2. Relayer `R1` submits a valid `GetResponseMessage`; `HandlerV2` calls `EvmHost.dispatchIncoming(GetResponse, R1)`, which stores the response receipt, invokes `onGetResponse` successfully, and pays `F` to `R1` — `_requestCommitments[commitment]` is left untouched (`evm/src/core/EvmHost.sol:824-847`).
3. Relayer `R2` (or `R1` again) submits a `GetTimeoutMessage` for the same request to `HandlerV2.handleGetRequestTimeouts`. Because `_requestCommitments[commitment]` still exists, `meta.sender != address(0)` and the timeout check `request.timeout() > state.timestamp` (`0 > state.timestamp`) is false, so processing proceeds to `host.dispatchTimeOut(GetRequestTimeout, meta, commitment)`.
4. `dispatchTimeOut` deletes the (now stale) entry, invokes `onGetTimeout`, and transfers `meta.fee == F` again to `R2` — the same fee `F` has now been paid out twice for one request.

**Caveat:** I could not fully trace, within the available tool budget, the exact semantics of the non-membership storage proof (`RESPONSE_RECEIPTS_STORAGE_PREFIX`) checked earlier in `handleGetRequestTimeouts` against the destination chain's committed state — it is possible this proof independently blocks the scenario for some configurations. The concrete, verified defect is the asymmetric cleanup: `dispatchIncoming(GetResponse, ...)` never deletes `_requestCommitments[commitment]` the way both `dispatchTimeOut` overloads explicitly do "for replay protection," leaving fee metadata for fulfilled requests permanently reusable.

### Citations

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

**File:** evm/src/core/EvmHost.sol (L885-900)
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

```

**File:** evm/src/core/HandlerV2.sol (L293-320)
```text
    function handleGetRequestTimeouts(IHost host, GetTimeoutMessage calldata message) external notFrozen(host) {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            GetRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            bytes32 commitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(commitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(RESPONSE_RECEIPTS_STORAGE_PREFIX, commitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(GetRequestTimeout(request, _msgSender()), meta, commitment);
        }
```
