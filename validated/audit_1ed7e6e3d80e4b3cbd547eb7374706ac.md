### Title
Double payout of GET-request relayer fee via response-then-timeout replay — missing request-commitment invalidation on response delivery ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatchIncoming(GetResponse)` pays out the relayer fee for a GET request but never deletes the corresponding `_requestCommitments[commitment]` entry, unlike every other terminal path (`dispatchTimeOut(GetRequestTimeout)`, `dispatchTimeOut(PostRequestTimeout)`) which explicitly deletes it as "replay protection." Combined with `HandlerV2.handleGetResponses` intentionally skipping any timeout check on GET responses ("don't check for timeouts because it's checked on Hyperbridge"), a GET request that is delivered late (past its deadline) can be settled twice: once through `handleGetResponses` and again through `handleGetRequestTimeouts`, paying the relayer fee out of host funds twice and re-invoking the destination app's `onGetTimeout` callback for an already-fulfilled request.

### Finding Description
`dispatchIncoming(GetResponse)` in `evm/src/core/EvmHost.sol` (lines 820-847): [1](#0-0) 
records a `_responseReceipts` entry for replay protection of the response itself, invokes `onGetResponse`, and pays the relayer fee straight out of `_requestCommitments[commitment].fee` — but it never clears `_requestCommitments[commitment]`.

Compare this with the timeout paths, which explicitly treat clearing `_requestCommitments` as the replay-protection mechanism: [2](#0-1) 

`HandlerV2.handleGetRequestTimeouts` gates on exactly this map to decide whether the request is "known": [3](#0-2) 
Since the response path never deletes the entry, `meta.sender != address(0)` still holds after a response has already been delivered, so the `UnknownMessage` guard does not stop a later timeout submission for the same request.

The second half of the flaw is that `handleGetResponses` deliberately performs no timeout check on the GET response before dispatching it: [4](#0-3) 
This means a response can legitimately be delivered and accepted even after the request's `timeout()` deadline has passed (there is no on-chain enforcement that Hyperbridge only ever produces responses within the deadline). This is the direct analog of the reported bug class: a value (the request/fee "freshness") is consumed without verifying it hasn't already been rendered stale/obsolete by a later or conflicting settlement path.

An attacker (any unprivileged relayer/caller — both handlers are `external`, unauthenticated by role) can then:
1. Wait for the GET request's `timeout()` to elapse.
2. Relay the (late) `GetResponse` via `handleGetResponses` — this succeeds because no timeout check exists there, pays the relayer fee, but leaves `_requestCommitments[commitment]` intact.
3. Submit `handleGetRequestTimeouts` for the same commitment using an **earlier** Hyperbridge state-commitment height `H0` — one at/after the request's timeout timestamp but before Hyperbridge had produced/recorded the response. At `H0` the non-membership proof for `RESPONSE_RECEIPTS_STORAGE_PREFIX || commitment` is genuinely valid (the response hadn't been recorded yet at that height), and `request.timeout() <= state.timestamp` also genuinely holds. Both handler-level checks pass with real, unforged proofs.
4. `dispatchTimeOut(GetRequestTimeout)` executes: it deletes `_requestCommitments[commitment]` (too late — the fee was already read and paid in step 2), invokes `onGetTimeout` on the destination app a second time for an already-settled request, and pays `meta.fee` a second time to `meta.sender`.

### Impact Explanation
This is a duplicate/double-settlement of protocol funds: the relayer fee attached to a GET request is paid out twice from the host's fee-token balance (once via the response path, once via the timeout path), draining funds beyond what was ever escrowed for that request. Worse, any application built on `onGetTimeout` that performs fund-moving logic (refunds, escrow release, cancellations) will be invoked twice for the same underlying request/order, since the host state does not mark the request as "settled" after a successful response. This falls squarely in the bounty's "replay/double-claim/double-settlement" and "unauthorized transaction/execution" categories — it requires no malicious relayer/prover/admin, only ordinary permissionless calls to two already-public entry points using real (not forged) state proofs at two different heights.

### Likelihood Explanation
The path requires no privileged actor and no invalid proof — every guard (`UnknownMessage`, `MessageNotTimedOut`, the MMR/trie proof verifications, challenge-period checks) can be satisfied honestly, because the missing invariant is not "is this proof forged" but "has this request already been settled." The only precondition is that a GET request response is delivered after its timeout deadline (plausible under any relayer/coprocessor delay, network congestion, or delayed challenge-period-bound consensus updates), which the code explicitly does not prevent (`handleGetResponses` skips timeout checks by design).

### Recommendation
Delete `_requestCommitments[commitment]` inside `dispatchIncoming(GetResponse)` immediately after the fee is paid (mirroring the pattern already used in both `dispatchTimeOut` overloads), so that once a response is settled, `handleGetRequestTimeouts`'s `meta.sender == address(0)` check correctly rejects any subsequent timeout submission for the same commitment. Additionally, consider having `handleGetResponses` reject responses whose underlying request has already timed out (using the state commitment's timestamp, consistent with how `handlePostRequests` already checks `timestamp >= leaf.request.timeout()`), rather than relying solely on an off-chain assumption that "it's checked on Hyperbridge."

### Proof of Concept
1. Dispatch a `GetRequest` from chain A with `timeout = T` and `fee = F`, paying `F` into `_requestCommitments[commitment]`.
2. Let `T` elapse without a response being relayed.
3. Hyperbridge later still produces the `GetResponse` (no protocol enforcement stops it). Relayer R1 submits it via `handlePostRequests`/`handleGetResponses` at Hyperbridge height `H1`; `dispatchIncoming(GetResponse)` runs `onGetResponse` and pays fee `F` to R1. `_requestCommitments[commitment]` is left untouched.
4. Obtain (or already have) a valid state-commitment proof for an earlier Hyperbridge height `H0 < H1` such that `state.timestamp(H0) >= T` (request already timed out at H0) and the response-receipt non-membership proof at `H0` is valid (response not yet recorded at that height) — both true by construction since the response was only recorded at H1.
5. Submit `handleGetRequestTimeouts` with height `H0`. `meta.sender != address(0)` (never cleared), `request.timeout() <= state.timestamp` passes, non-membership proof passes → `dispatchTimeOut(GetRequestTimeout)` deletes the commitment now, calls `onGetTimeout` on the app a second time, and pays fee `F` again to `meta.sender`.
6. Result: fee `F` paid out twice, and the app's timeout logic executed for an already-fulfilled request.

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

**File:** evm/src/core/HandlerV2.sol (L217-247)
```text
    function handleGetResponses(IHost host, GetResponseMessage calldata message) external notFrozen(host) {
        uint256 timestamp = block.timestamp;
        uint256 delay = timestamp - host.stateMachineCommitmentUpdateTime(message.proof.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        uint256 responsesLength = message.responses.length;
        MerkleMountainRange.Leaf[] memory leaves = new MerkleMountainRange.Leaf[](responsesLength);

        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // don't check for timeouts because it's checked on Hyperbridge

            // known request? also serves as source check
            FeeMetadata memory meta = host.requestCommitments(leaf.response.request.hash());
            if (meta.sender == address(0)) revert UnknownMessage();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.response.hash());
        }

        bytes32 root = host.stateMachineCommitment(message.proof.height).overlayRoot;
        if (root == bytes32(0)) revert StateCommitmentNotFound();
        bool valid = MerkleMountainRange.VerifyProof(root, message.proof.multiproof, leaves, message.proof.leafCount);
        if (!valid) revert InvalidProof();

        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // duplicate response?
            if (host.responseReceipts(leaf.response.request.hash()).relayer != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.response, _msgSender());
        }
    }
```

**File:** evm/src/core/HandlerV2.sol (L293-321)
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
    }
```
