Confirmed: `dispatch(DispatchGet)` sets `_requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee})` [1](#0-0)  exactly mirroring `dispatch(DispatchPost)` [2](#0-1) , so GET requests carry a real, refundable `fee` escrowed in `_requestCommitments`.

### Title
GetResponse delivery never clears `_requestCommitments`, enabling a second timeout-refund payout for an already-fulfilled GET request - (File: `evm/src/core/EvmHost.sol`)

### Summary
`dispatchIncoming(GetResponse, address relayer)` pays the relayer their fee straight out of `_requestCommitments[commitment].fee` but — unlike every other terminal dispatch path in the same contract — never deletes `_requestCommitments[commitment]` afterward. This leaves the escrowed fee metadata "live" for a request that has already been fully serviced and paid for, so it can later be consumed a second time via the GET-timeout path, refunding the same escrowed fee to the payer even though it was already paid out to a relayer.

### Finding Description
Every other success/terminal handler in `EvmHost.sol` clears the corresponding commitment/receipt as part of settling the fee, preventing reuse:
- `dispatchTimeOut(GetRequestTimeout, ...)` deletes `_requestCommitments[commitment]` before invoking the callback [3](#0-2) .
- `dispatchTimeOut(PostRequestTimeout, ...)` does the same [4](#0-3) .

But `dispatchIncoming(GetResponse, ...)` only writes a `_responseReceipts[commitment]` marker and pays the relayer fee, and leaves `_requestCommitments[commitment]` fully intact:
```solidity
function dispatchIncoming(GetResponse memory response, address relayer) external restrict(_hostParams.handler) {
    bytes32 commitment = response.request.hash();
    _responseReceipts[commitment] = ResponseReceipt({relayer: relayer, responseCommitment: response.hash()});
    (bool success,) = _bytesToAddress(response.request.from)
        .call(abi.encodeWithSelector(IApp.onGetResponse.selector, IncomingGetResponse(response, relayer)));
    if (!success) { delete _responseReceipts[commitment]; return; }
    uint256 fee = _requestCommitments[commitment].fee;
    if (fee != 0) { IERC20(feeToken()).safeTransfer(relayer, fee); }
    emit GetRequestHandled({commitment: commitment, relayer: relayer});
}
``` [5](#0-4) 

`HandlerV2.handleGetResponses()` only guards against re-delivering the *same response* by checking `responseReceipts` before calling `dispatchIncoming` [6](#0-5) ; it does not touch `requestCommitments`, and there is no code path that clears `_requestCommitments[commitment]` once a response has been successfully delivered.

`HandlerV2.handleGetRequestTimeouts()` independently treats the request as still "in flight" as long as `requestCommitments(commitment).sender != address(0)`, and only checks a non-membership proof of `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` against Hyperbridge's own state root at the supplied height/proof — a check against a *different, remote* trie than the local `_responseReceipts` mapping that already recorded delivery on this EVM host:
```solidity
FeeMetadata memory meta = host.requestCommitments(commitment);
if (meta.sender == address(0)) revert UnknownMessage();
bytes[] memory keys = new bytes[](1);
keys[0] = bytes.concat(RESPONSE_RECEIPTS_STORAGE_PREFIX, commitment);
PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
if (entry.value.length != 0) revert InvalidProof();
host.dispatchTimeOut(GetRequestTimeout(request, _msgSender()), meta, commitment);
``` [7](#0-6) 

Because the local `_requestCommitments` entry was never cleared after a successful `GetResponse` delivery, `meta.sender != address(0)` remains true forever, and nothing in this flow re-checks the local `_responseReceipts[commitment]` that already proves the request was serviced. If a state height/proof can be produced where the remote receipts-prefix key is (still) absent — plausible since the two receipt bookkeeping systems (local EVM `_responseReceipts` vs. remote pallet-ismp state referenced by `RESPONSE_RECEIPTS_STORAGE_PREFIX`) are decoupled and not guaranteed to be written in lock-step for GET requests resolved purely by MMR-proved `GetResponse` objects — `dispatchTimeOut(GetRequestTimeout, ...)` will execute for a request that was already answered and already paid out.

### Impact Explanation
This is a double-settlement of the same escrowed fee for one GET request: the relayer is paid via `dispatchIncoming(GetResponse, ...)`, and then the original `payer` is refunded the identical fee a second time via the timeout path, with no new fee ever having been collected for that second payout. This is a direct loss/duplication of protocol/user funds and matches the bounty's "double-claim / double-settlement" and "stealing or loss of funds" categories, since the fee escrow account is drained beyond what was ever deposited for the request. It also triggers `onGetTimeout` on the app a second time for a request the app already believed was resolved via `onGetResponse`, which is a form of unauthorized/duplicate execution against application state if the app is not defensively idempotent.

### Likelihood Explanation
The path requires only a permissionless relayer/caller submitting a valid `GetRequestTimeoutMessage` after a `GetResponse` for the same commitment has already been delivered — no privileged actor, malicious peer, or governance action is needed [8](#0-7) . The trigger condition (a non-membership proof against the remote receipts-prefix key still validating after local delivery) depends on how/when pallet-ismp writes that specific storage key for GET flows resolved through the MMR-proved response mechanism used here; this repo snapshot does not let me fully confirm the write-timing relationship between the two receipt systems, so likelihood should be validated against the live pallet-ismp GET-response code path before treating this as certain, but the missing `delete _requestCommitments[commitment]` on the success path is a clear, local defect regardless.

### Recommendation
Delete `_requestCommitments[commitment]` inside `dispatchIncoming(GetResponse, ...)` immediately after (or instead of relying solely on) reading the fee for payout, mirroring the pattern already used in both `dispatchTimeOut` overloads. Additionally, have `handleGetRequestTimeouts` (or `dispatchTimeOut(GetRequestTimeout, ...)`) check the local `_responseReceipts[commitment]` and revert if a response has already been recorded, rather than relying only on a non-membership proof against a separate, remote trie.

### Proof of Concept
1. App calls `dispatch(DispatchGet)` with `fee = F`; `_requestCommitments[commitment] = {sender: payer, fee: F}` is stored [1](#0-0) .
2. Relayer A delivers the response via `handleGetResponses` → `dispatchIncoming(GetResponse, relayerA)`; `onGetResponse` succeeds, `_responseReceipts[commitment]` is set, and relayer A is paid `F` from `feeToken()` [9](#0-8) . `_requestCommitments[commitment]` still equals `{payer, F}`.
3. After the request's `timeoutTimestamp` has passed, an actor submits `handleGetRequestTimeouts` with a state height/proof where the pallet-ismp `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` key is absent (a proof unrelated to this EVM host's own `_responseReceipts`) [7](#0-6) .
4. `dispatchTimeOut(GetRequestTimeout, ...)` executes, calling `onGetTimeout` on the app a second time and, if the callback succeeds, refunding `F` again to `payer` [10](#0-9) . The protocol has now paid out `2F` for a fee escrow of `F`.

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

**File:** evm/src/core/EvmHost.sol (L946-948)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```

**File:** evm/src/core/EvmHost.sol (L999-1001)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: _msgSender(), fee: get.fee});
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
