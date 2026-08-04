## Title
Double-payment of the GET-request fee via late response delivery + stale-height timeout proof - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatchIncoming(GetResponse, relayer)` pays out `_requestCommitments[commitment].fee` to the delivering relayer but never deletes `_requestCommitments[commitment]` afterward. Because `HandlerV2.handleGetResponses` deliberately skips any timeout check ("don't check for timeouts because it's checked on Hyperbridge"), a response can still be accepted and paid after the request's `timeout()` has passed. Since old `_stateCommitments` entries are never pruned, an attacker can then also present a **stale, pre-response** state height to `handleGetRequestTimeouts` with a valid non-membership proof (correct for that older height, though the response has since landed on-chain at a later height) and trigger `dispatchTimeOut(GetRequestTimeout, meta, commitment)`, which refunds the very same `fee` a second time to `meta.sender`. The same fee is paid out twice from the host's fee-token balance, to two different beneficiaries — the exact "distribute repeatedly, drain the balance" pattern from the referenced NFTX report, just realized through a stale-proof double-claim instead of literal re-entrancy.

### Finding Description
1. `dispatchIncoming(GetResponse memory response, address relayer)` [1](#0-0)  sets `_responseReceipts[commitment]` (replay guard for this host's own state), invokes `onGetResponse` on the destination module, and — on success — pays `_requestCommitments[commitment].fee` to `relayer`. It never clears `_requestCommitments[commitment]`.
2. `HandlerV2.handleGetResponses` explicitly does not check the request's timeout before calling `dispatchIncoming`: "don't check for timeouts because it's checked on Hyperbridge" [2](#0-1) . So a response delivered after `request.timeout()` has elapsed is still accepted and its fee still paid.
3. Separately, `handleGetRequestTimeouts` proves non-delivery of the response by verifying a **non-membership proof against `host.stateMachineCommitment(message.height)`** for an attacker-chosen historical height [3](#0-2) . `storeStateMachineCommitment`/`setConsensusState` write every height into `_stateCommitments[stateMachineId][height]` and old entries are never pruned except via explicit governance veto [4](#0-3) , so an old height that predates the (late) response delivery remains available and still produces a *valid* non-membership proof, even though the response has since arrived at a newer height.
4. `dispatchTimeOut(GetRequestTimeout, meta, commitment)` [5](#0-4)  deletes `_requestCommitments[commitment]` and, on successful `onGetTimeout` callback, refunds `meta.fee` to `meta.sender`.
5. Because step 1 never cleared `_requestCommitments[commitment]`, `meta` (with the original non-zero `fee`) is still retrievable by `HandlerV2.handleGetRequestTimeouts` at the time it queries `host.requestCommitments(commitment)` [6](#0-5) , allowing the same `fee` to be paid out a second time to `meta.sender` after it was already paid to `relayer` in step 1.

The net effect: the fee token balance held for this request is paid out twice — once as a relayer reward, once as a "timeout refund" — for a single logical request, draining the host's fee-token balance analogous to the NFTX `distribute` bug repeatedly paying one fee receiver.

### Impact Explanation
This is a direct loss of protocol/fee-token funds: the same escrowed `fee` amount is disbursed twice from `EvmHost`'s fee-token balance to two different recipients (relayer and original sender) for one request, at no cost beyond normal relaying/timeout-proving activity. Any unprivileged relayer/user who can deliver a late GET response and later submit a timeout proof against an older, still-stored state commitment can trigger this. It falls squarely within "stealing or loss of funds" / "duplicate settlement/double-claim" in the bounty's impact gate.

### Likelihood Explanation
Requires: (a) a GET request whose response is delivered after its nominal timeout (plausible under normal relaying delays or an attacker deliberately delaying/expediting either message), and (b) at least one older `_stateCommitments` entry for the destination chain that predates the response and still proves non-membership. Both conditions rely only on public, permissionless entrypoints (`handleGetResponses`, `handleGetRequestTimeouts`) — no malicious relayer, prover, or governance actor is needed; a single actor (or even two uncoordinated relayers pursuing their own timeout/response races) can trigger it.

### Recommendation
- Delete/zero `_requestCommitments[commitment]` (or otherwise mark the fee as consumed) inside `dispatchIncoming(GetResponse, ...)` immediately after — or ideally before — paying the relayer, mirroring the CEI pattern already used elsewhere.
- Add an explicit timeout check in `handleGetResponses`/`dispatchIncoming(GetResponse)` so a response cannot be accepted (and its fee paid) once `request.timeout()` has elapsed.
- Additionally consider requiring `handleGetRequestTimeouts` to use only the *latest* known state commitment for the destination (or track a per-commitment "highest proven height" watermark) so a stale, pre-response height cannot be replayed to derive a false non-membership proof.

### Proof of Concept
1. Dispatch a `GetRequest` from `EvmHost` with a non-zero `fee` and `timeout` T.
2. Wait until `block.timestamp > T` (request now timed out on Hyperbridge side too, but `EvmHost` performs no local timeout check).
3. Relayer submits `HandlerV2.handleGetResponses` with a valid MMR proof of the (late) `GetResponse` at some Hyperbridge height H1 → `EvmHost.dispatchIncoming(GetResponse,...)` succeeds, pays `fee` to `relayer`, but leaves `_requestCommitments[commitment]` intact [7](#0-6) .
4. Same or different actor submits `HandlerV2.handleGetRequestTimeouts` using an older previously-stored `StateMachineHeight` H0 < H1 for the destination chain, where a non-membership proof of `RESPONSE_RECEIPTS_STORAGE_PREFIX || commitment` is still valid (because at H0 the response hadn't landed yet) and `request.timeout() <= state(H0).timestamp` (already true since the request is timed out) [8](#0-7) .
5. `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` succeeds and refunds `fee` a second time to `meta.sender` [9](#0-8) .
6. Result: `fee` was paid out twice from `EvmHost`'s fee-token holdings for a single request.

### Citations

**File:** evm/src/core/EvmHost.sol (L776-788)
```text
    function setConsensusState(bytes memory state, StateMachineHeight memory height, StateCommitment memory commitment)
        public
        restrict(_hostParams.admin)
    {
        if (!_canReinitConsensus()) revert UnauthorizedAction();

        _consensusState = state;
        _consensusUpdateTimestamp = block.timestamp;

        _stateCommitments[height.stateMachineId][height.height] = commitment;
        _stateCommitmentsUpdateTime[height.stateMachineId][height.height] = block.timestamp;
        _latestStateMachineHeight[height.stateMachineId] = height.height;
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

**File:** evm/src/core/EvmHost.sol (L849-877)
```text
    /**
     * @dev Dispatch an incoming GET timeout to the source module.
     * @notice Does not refund any protocol fees.
     * @param timeout - timed-out get request bundled with the relayer that submitted the timeout proof
     * @param meta - fee metadata for the original request
     * @param commitment - request commitment
     */
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

**File:** evm/src/core/HandlerV2.sol (L217-246)
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
