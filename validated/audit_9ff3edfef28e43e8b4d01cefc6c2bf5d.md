## Title
GET-response relayer fee is not cleared after payout, allowing a stale `FeeMetadata` to be paid out a second time via the GET-timeout path - (File: `evm/src/core/EvmHost.sol`)

### Summary
The external report's core defect is a one-time-use resource (an `entropy` `sequenceNumber`) that a contract's own accounting assumes can be reused, causing a state/fund-flow to be executed against stale bookkeeping. The closest local analog in Hyperbridge is in `EvmHost.dispatchIncoming(GetResponse, ...)`: after a `GetResponse` is successfully delivered and the relayer fee is paid out of `_requestCommitments[commitment].fee`, that `FeeMetadata` entry is never deleted, unlike every other completion path in the same contract (`dispatchTimeOut` for both GET and POST deletes `_requestCommitments` before acting). [1](#0-0) 

### Finding Description
`dispatchIncoming(GetResponse memory response, address relayer)` is the only completion handler in `EvmHost.sol` that reads `_requestCommitments[commitment].fee` to pay the relayer, but does not `delete _requestCommitments[commitment]` afterward: [1](#0-0) 

Compare this with `dispatchTimeOut` for `GetRequestTimeout` and `PostRequestTimeout`, which both explicitly `delete _requestCommitments[commitment]` as "replay protection" before invoking the app callback, and only restore it if the callback fails: [2](#0-1) 

Because `dispatchIncoming(GetResponse,...)` leaves the `FeeMetadata` (`sender`, `fee`) sitting in `_requestCommitments` after a successful delivery, the state that `handleGetRequestTimeouts` uses to decide "is this a known request with a fee to refund" is unchanged by the fact that the request was already fulfilled: [3](#0-2) 

`handleGetRequestTimeouts` only checks:
1. that the timeout timestamp has elapsed on the tracked destination state (`request.timeout() > state.timestamp`), and
2. a non-membership proof that `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` is absent from the state root at `message.height` — i.e., absence of a response receipt *at that specific proven height*.

It never checks `EvmHost._responseReceipts[commitment]` (the host's own local receipt, set by `dispatchIncoming(GetResponse,...)` at line 827-830) before calling `host.dispatchTimeOut(...)`. If a relayer already delivered the `GetResponse` and collected the fee, but the timeout-message submitter supplies a non-membership proof against an *earlier* state height (before the response was recorded on the destination), the non-membership check can pass even though the request has already been fully serviced and paid on this host. `dispatchTimeOut(GetRequestTimeout,...)` will then delete `_requestCommitments[commitment]` and refund `meta.fee` to `meta.sender` a second time for a fee that has already been transferred to the relayer once: [4](#0-3) 

This is structurally the same bug shape as the QuailFinance report: a value that should be single-use (the fee tied to a specific commitment) is consumed by one path (`dispatchIncoming` GetResponse payout) without updating the shared piece of state (`_requestCommitments`) that a second, independent path (`dispatchTimeOut`) relies on to decide whether it is safe to act again — except here, instead of the second call reverting (DoS), the second call silently succeeds and pays out funds a second time (double payment / fund loss from the fee token balance), because the check that would prevent it (`_responseReceipts`) is asserted only in the handler that processes `handleGetResponses`, not in the timeout handler that processes `handleGetRequestTimeouts`.

### Impact Explanation
This allows the relayer fee for a single `GetRequest` to be paid out twice: once through normal delivery (`handleGetResponses` → `dispatchIncoming(GetResponse,...)`), and again through the timeout path (`handleGetRequestTimeouts` → `dispatchTimeOut(GetRequestTimeout,...)`) if a non-membership proof at a stale height can be constructed/submitted. This is a direct loss of `feeToken()` funds from the host contract to an unintended beneficiary (double settlement), matching the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" categories.

### Likelihood Explanation
Both `handleGetResponses` and `handleGetRequestTimeouts` are explicitly documented as permissionless entrypoints callable by anyone. The window for the exploit depends on whether a valid non-membership proof for an earlier committed height (prior to response delivery) remains constructible/acceptable after the response has already landed — this requires that the host still tracks/accepts a `StateMachineHeight` earlier than the one at which the response was recorded, which is plausible given the multi-height `stateMachineCommitment` tracking in `EvmHost`. Full exploitability depends on relayer/state-machine height-selection semantics that were not completely traceable in the available index; this should be validated by a background engineer against the exact height-ordering guarantees of `verify_non_membership`/`PolkadotTrie.VerifyProof` for GET timeouts.

### Recommendation
In `EvmHost.dispatchIncoming(GetResponse memory response, address relayer)`, delete `_requestCommitments[commitment]` immediately after (or as part of) a successful relayer fee payout, mirroring the pattern used in `dispatchTimeOut`. Additionally, `handleGetRequestTimeouts` (and/or `EvmHost.dispatchTimeOut(GetRequestTimeout,...)`) should explicitly check `_responseReceipts[commitment]` is unset before processing a timeout, so that a request already known to be fulfilled locally can never be timed out and refunded again, regardless of which state height the non-membership proof targets.

### Proof of Concept
Not independently reproducible from the available index — a concrete PoC would require constructing (a) a valid `GetResponseMessage` delivered through `handleGetResponses` to trigger the fee payout in `dispatchIncoming(GetResponse,...)`, and (b) a `GetTimeoutMessage` with a non-membership proof against a state height recorded on the host prior to the response's inclusion height, then showing `handleGetRequestTimeouts` accepts it and `dispatchTimeOut` refunds `meta.fee` a second time. This requires end-to-end trie/proof tooling (`PolkadotTrie.VerifyProof`) not available in this read-only review; a background Devin session with the full repo and test harness (e.g. `evm/test` or the Rust `testsuite` fixtures) should attempt to build this scenario to confirm exploitability, in particular whether the host can be made to retain/accept a pre-response height commitment usable for the non-membership proof after delivery has already occurred.

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

**File:** evm/src/core/HandlerV2.sol (L288-321)
```text
    /**
     * @dev Check the provided Get request timeouts, then dispatch to modules
     * @param host - Ismp host
     * @param message - batch get request timeouts
     */
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
    }
```
