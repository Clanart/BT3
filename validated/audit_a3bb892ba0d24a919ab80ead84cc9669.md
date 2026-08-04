## Analysis

The core broken invariant in the external report is: **a successful "unlock" (settlement) event fails to clear the per-item locked/pending state, so later code that gates on that same state still treats the item as active — enabling either duplicate settlement or false total-count corruption.**

Searching Hyperbridge's EVM host for the same pattern (settle-then-forget-to-clear) surfaces a direct structural analog in `EvmHost.sol`'s GET-response settlement path.

### Title
Missing clearance of `_requestCommitments[commitment]` after GET-response fee settlement — inconsistent with the explicit clear performed on the timeout path - (File: `evm/src/core/EvmHost.sol`)

### Finding Description
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` pays the relayer their fee straight out of `_requestCommitments[commitment].fee` after a successful `onGetResponse` callback, but never deletes or zeroes `_requestCommitments[commitment]`: [1](#0-0) 

Compare this to the two `dispatchTimeOut` variants, which settle the exact same `FeeMetadata` state and explicitly `delete _requestCommitments[commitment]` up front as "replay protection", re-storing it only if the module callback fails: [2](#0-1) 

This is the same class of bug as the LlamaLocker report: a value that should be cleared once its corresponding action (unlock / fee settlement) succeeds is left intact, so any other code path that keys off `_requestCommitments[commitment]` being "known"/non-zero (e.g. `meta.sender == address(0)` checks in `HandlerV2.handleGetRequestTimeouts` and `handlePostRequestTimeouts`, and `fundRequest`) continues to treat an already-settled request as live indefinitely: [3](#0-2) [4](#0-3) 

The single-message duplicate-delivery vector (calling `dispatchIncoming` twice for the same commitment in one batch, à la the two-`tokenIdA` PoC in the report) *is* blocked here, because `HandlerV2.handleGetResponses`/`handlePostRequests` check `host.responseReceipts(...)`/`host.requestReceipts(...)` immediately before each `dispatchIncoming` call, and `dispatchIncoming` sets that receipt synchronously (CEI) before invoking the external module callback: [5](#0-4) 

So the direct "call unlock twice in one array" reentrant-style trick from the report does not reproduce here. What remains directly provable from the code is the asymmetry itself: `dispatchIncoming(GetResponse)` is the only successful-settlement path for `_requestCommitments` that does not clear the entry, while every other successful-settlement path for the same mapping (`dispatchTimeOut` for both GET and POST) does clear it.

### Impact Explanation
Because `_requestCommitments[commitment]` survives a successful GET-response delivery, `meta.sender != address(0)` stays true forever for that commitment. `fundRequest` and `handleGetRequestTimeouts` both keep treating the commitment as an outstanding, payable request after it has already been paid out — `fundRequest`'s own docstring even acknowledges this ("if called on an already delivered request, these funds will be seen as a donation"), which is itself evidence that stale, uncleared settlement state is a known side effect of this design gap. Whether a full double-refund (via a subsequently-accepted `handleGetRequestTimeouts` non-membership proof) is independently reachable depends on the semantics of the `RESPONSE_RECEIPTS_STORAGE_PREFIX` non-membership proof checked in `handleGetRequestTimeouts`, which I was not able to fully trace to confirm it is checked against the exact same state root that reflects this host's own `_responseReceipts` write. I flag this uncertainty explicitly rather than assert it.

### Likelihood Explanation
The asymmetric-clear pattern is deterministic and always occurs on every successful GET response — no adversarial timing or reentrancy is required to trigger the missing `delete`. Whether it converts into duplicate fund movement depends on the (unverified) proof semantics noted above, so likelihood of the state-not-cleared condition is high/certain, but likelihood of a full duplicate payout is undetermined without further trie-proof analysis.

### Recommendation
Mirror the pattern already used in `dispatchTimeOut`: after a successful `onGetResponse` callback and fee payout in `dispatchIncoming(GetResponse ...)`, `delete _requestCommitments[commitment]` (or zero out `.fee`) so that `fundRequest` and any timeout/replay path treat the commitment as fully settled, consistent with how `_requestCommitments` is cleared on the timeout branches.

### Proof of Concept
1. User dispatches a GET request via `dispatch(DispatchGet)`; `_requestCommitments[commitment] = FeeMetadata({sender, fee})` is recorded (`evm/src/core/EvmHost.sol:974-1013`).
2. A relayer submits `handleGetResponses`, which calls `dispatchIncoming(GetResponse, relayer)`. The relayer is paid `fee` from `_requestCommitments[commitment].fee`, but the entry is never deleted (`evm/src/core/EvmHost.sol:824-847`).
3. Any account can call `fundRequest(commitment, amount)` afterward; because `metadata.sender != address(0)` still holds, it succeeds and funds are absorbed with no further consumer (`evm/src/core/EvmHost.sol:1031-1051`) — confirmed as intended-but-lossy behavior by the function's own docstring.
4. This demonstrates the exact bug class from the report (post-success state not cleared, stale state still gates further logic); full confirmation of a double relayer/refund payout would require tracing the exact state root used in `handleGetRequestTimeouts`'s non-membership proof, which was not completed within available tool budget.

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

**File:** evm/src/core/EvmHost.sol (L1031-1051)
```text
    function fundRequest(bytes32 commitment, uint256 amount) external payable notFrozen {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                amount, path, address(this), block.timestamp
            );
        } else {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), amount);
        }

        FeeMetadata memory metadata = _requestCommitments[commitment];
        if (metadata.sender == address(0)) revert UnknownRequest();

        metadata.fee += amount;
        _requestCommitments[commitment] = metadata;

        emit RequestFunded({commitment: commitment, newFee: metadata.fee});
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
