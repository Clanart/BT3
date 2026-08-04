## Analysis

The `PreimageOracle` bug's core pattern — a completed operation's bookkeeping isn't finalized/cleared, allowing a second, contradictory claim against the same state — has a direct, locally provable analog in `EvmHost.sol`'s GET-request fee accounting.

### Title
Missing cleanup of `_requestCommitments` after a successful GET response allows the relayer fee to be paid out twice — once to the relayer and once again as a timeout refund - (File: `evm/src/core/EvmHost.sol`)

### Summary
When a GET request is dispatched, its relayer fee is stored in `_requestCommitments[commitment]` [1](#0-0) . Two independent, unprivileged code paths can pay out that same fee: `dispatchIncoming(GetResponse, relayer)` pays it to the relayer that delivered the response, and `dispatchTimeOut(GetRequestTimeout, meta, commitment)` refunds it to the original payer. The response-delivery path never clears `_requestCommitments[commitment]` after paying the relayer, while the timeout path only checks `meta.sender != address(0)` before paying out — a condition that remains true because the entry was never deleted.

### Finding Description
In `dispatchIncoming(GetResponse memory response, address relayer)`: [2](#0-1) 

Note that after a successful `onGetResponse` callback, the function reads `_requestCommitments[commitment].fee` and transfers it to `relayer`, but **never deletes** `_requestCommitments[commitment]`.

Compare this with the two timeout dispatch functions, which explicitly delete the commitment record before invoking the module callback and only restore it if the callback reverts: [3](#0-2) 

`handleGetRequestTimeouts` in `HandlerV2.sol` gates the timeout path solely on `host.requestCommitments(commitment)` still being non-zero: [4](#0-3) 

Because the GetResponse path never clears this same mapping entry, `meta.sender` remains non-zero even after the fee has already been paid out to a relayer, so the `UnknownMessage` guard does not stop a subsequent timeout claim for the very same request.

Additionally, `handleGetResponses` explicitly skips the timeout check when accepting a response ("don't check for timeouts because it's checked on Hyperbridge"): [5](#0-4) 

This means a response can legitimately be delivered and paid *after* the request's `timeoutTimestamp` has already elapsed, at which point `handleGetRequestTimeouts` also becomes eligible to process the same request (the `request.timeout() > state.timestamp` check in `HandlerV2.sol` line 306 is satisfied). The timeout path's only remaining defense is a non-membership proof of a response receipt against a *destination*-chain state commitment; an attacker can select any already-finalized destination height whose timestamp is ≥ the request's timeout but which predates whatever storage change corresponds to the delivered response, since the two systems (source-chain `_responseReceipts`/`_requestCommitments` bookkeeping and the destination-chain proof) are not the same key space and are not cross-checked against each other.

### Impact Explanation
This allows the `feeToken()` relayer fee for a single GET request to be paid out twice from the `EvmHost` contract's fee-token balance: once to whoever delivers the (possibly late) `GetResponse`, and again as a "timeout refund" to `meta.sender`. This is a direct, unauthorized loss of protocol/fee-token funds and a double-settlement of the same commitment — squarely within the bounty's "stealing or loss of funds" and "replay/double-claim/double-settlement" categories. No relayer, prover, or admin compromise is required; a single unprivileged actor holding both roles (payer and self-relayer, both of which are permitted operations) can trigger it, or two colluding unprivileged actors.

### Likelihood Explanation
The path requires only standard, permissionless entry points (`handleGetResponses` and `handleGetRequestTimeouts`, both callable by anyone) and a real but plausible timing condition: response delivery occurring after the nominal timeout, combined with a destination-chain state proof at a height preceding the response record. This is a legitimate, code-reachable outcome, not a contrived edge case — the code comment in `HandlerV2.sol` even documents that timeout checks are intentionally skipped for GET responses, and `dispatchIncoming(GetResponse,...)` unconditionally omits the corresponding cleanup of `_requestCommitments`, unlike its two timeout-dispatch siblings.

### Recommendation
`dispatchIncoming(GetResponse memory response, address relayer)` should `delete _requestCommitments[commitment]` immediately after successfully paying the relayer fee (mirroring the cleanup pattern already used in both `dispatchTimeOut` variants), and/or `handleGetRequestTimeouts`/`dispatchTimeOut(GetRequestTimeout,...)` should additionally verify that `_responseReceipts[commitment]` is unset before allowing a refund, so a request that has already been fee-paid via a delivered response can never also be refunded via timeout.

### Proof of Concept
1. Dispatch a GET request on chain S targeting chain D with `fee = F`; `_requestCommitments[commitment] = {sender: payer, fee: F}` is recorded [1](#0-0) .
2. Let the request's `timeoutTimestamp` elapse without delivery.
3. Submit `handleGetResponses` with a valid state proof from D (still accepted — no timeout check per the code comment) [6](#0-5) ; `dispatchIncoming(GetResponse, relayer)` succeeds and pays `F` in `feeToken()` to `relayer`, but leaves `_requestCommitments[commitment]` intact [7](#0-6) .
4. Submit `handleGetRequestTimeouts` for the same request with a non-membership proof against an earlier finalized D-height whose timestamp is ≥ the request timeout but predates the actual response record; `meta.sender` is still `payer` (non-zero), so the `UnknownMessage` check passes [8](#0-7) , and `dispatchTimeOut` refunds `F` again to `payer` [9](#0-8) .
5. Total `F * 2` has been paid out of the host's fee-token balance for a single request's fee of `F`.

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

**File:** evm/src/core/EvmHost.sol (L999-1001)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
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
