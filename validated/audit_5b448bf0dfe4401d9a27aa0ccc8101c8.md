This confirms the vulnerability. The Substrate ISMP `timeout::handle` for `TimeoutMessage::Get` explicitly guards against this race by checking `host.response_receipt(&response).is_some()` and rejecting the timeout with `GetResponseAlreadyReceived` if a response was already delivered [1](#0-0) . The EVM `HandlerV2.handleGetRequestTimeouts` path has **no equivalent check** against the host's own `responseReceipts` — it only checks a foreign non-membership trie proof against the destination chain's storage, and `EvmHost.dispatchIncoming(GetResponse,...)` never deletes `_requestCommitments[commitment]` after successfully paying the relayer fee [2](#0-1) , leaving `meta.sender != address(0)` so a later timeout still passes the "known request" check in `HandlerV2.handleGetRequestTimeouts` [3](#0-2) .

### Title
GET request double-settlement: `handleGetRequestTimeouts` on EVM never checks `responseReceipts`, allowing fee to be paid twice and `onGetTimeout` to fire after `onGetResponse` already succeeded - (File: `evm/src/core/HandlerV2.sol`, `evm/src/core/EvmHost.sol`)

### Summary
On the EVM ISMP host, a GET request whose response has already been successfully delivered via `handleGetResponses`/`dispatchIncoming(GetResponse,...)` can still be timed out via `handleGetRequestTimeouts`/`dispatchTimeOut(GetRequestTimeout,...)`, because neither function checks the host's own `_responseReceipts` mapping before processing the timeout, and the relayer-fee metadata (`_requestCommitments[commitment]`) is never cleared after a successful response delivery.

### Finding Description
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` sets `_responseReceipts[commitment]` and pays the relayer fee from `_requestCommitments[commitment].fee`, but it never deletes `_requestCommitments[commitment]`: [4](#0-3) 

Compare this to `dispatchTimeOut(GetRequestTimeout,...)`, which explicitly comments "replay protection" and deletes `_requestCommitments[commitment]` before invoking the app's callback and refunding the fee to the original sender: [5](#0-4) 

Because `_requestCommitments[commitment]` is left populated after a successful GET response, `HandlerV2.handleGetRequestTimeouts` still treats the request as "known" (`meta.sender == address(0)` check passes since `meta.sender != address(0)`): [3](#0-2) 

The only guard against a late timeout is a non-membership proof of `RESPONSE_RECEIPTS_STORAGE_PREFIX` against the *destination chain's* storage trie — this is irrelevant to whether the *source chain* (this EVM host) has already delivered/paid out the response. It does not consult `host.responseReceipts(commitment)` on the local host at all. The Substrate implementation of the identical flow does perform this local check (`host.response_receipt(&response).is_some()` → `GetResponseAlreadyReceived`) [1](#0-0) , confirming this is the intended invariant that the EVM path fails to enforce.

### Impact Explanation
An unprivileged relayer/attacker can, for a GET request whose response was already delivered and whose relayer fee already paid out via `handleGetResponses`, submit a timeout proof (once the destination's on-chain state at the proven height genuinely lacks its own unrelated "ResponseReceipts" child-trie entry, which is the normal case for a plain state-read query against most destination chains) through `handleGetRequestTimeouts`. This:
- Invokes `onGetTimeout` on the source application a second time for a request that already succeeded via `onGetResponse`, causing duplicate state transitions/duplicate settlement in the calling `IApp` (e.g., a module that both released outputs on `onGetResponse` and refunds/reverts escrow on `onGetTimeout` would apply both effects).
- Refunds `meta.fee` a second time to `meta.sender`, i.e., the same fee amount is paid out twice from host reserves: once to the relayer in `dispatchIncoming(GetResponse,...)`, and again to the request's payer in `dispatchTimeOut(GetRequestTimeout,...)`.

This is a direct fund-duplication / double-settlement bug matching the bounty's "replay/double-claim/double-settlement" impact class.

### Likelihood Explanation
The path is fully permissionless — `handleGetRequestTimeouts` can be called by anyone with a valid timeout proof, no relayer/prover/governance compromise required. The only precondition is that the timeout timestamp has elapsed and a non-membership proof can be produced against the destination's `RESPONSE_RECEIPTS_STORAGE_PREFIX` slot, which is a proof about an unrelated storage location on the destination and will typically be satisfiable regardless of whether the GET response was already delivered and paid on the source EVM host.

### Recommendation
In `EvmHost.dispatchIncoming(GetResponse,...)`, delete `_requestCommitments[commitment]` after paying out the relayer fee (mirroring the "replay protection" pattern already used in `dispatchTimeOut`). Additionally, `HandlerV2.handleGetRequestTimeouts` should check `host.responseReceipts(commitment)` locally and revert (e.g., with a `GetResponseAlreadyReceived`-style error) if a response has already been recorded on this host, matching the guard already present in the Substrate `timeout::handle` implementation.

### Proof of Concept
1. Dispatch a `GetRequest` from the EVM source host with a nonzero relayer `fee`; `_requestCommitments[commitment] = {sender, fee}` is stored.
2. A relayer submits a valid `GetResponseMessage` before the request times out. `HandlerV2.handleGetResponses` → `EvmHost.dispatchIncoming(GetResponse,...)` succeeds: `onGetResponse` is called, and `fee` is paid to the relayer. Note `_requestCommitments[commitment]` is **not** deleted [6](#0-5) .
3. After `request.timeout()` has elapsed on the destination's committed state, an attacker constructs a `GetTimeoutMessage` containing the same request and a non-membership proof of `RESPONSE_RECEIPTS_STORAGE_PREFIX ++ commitment` against the destination's real (unrelated) storage trie — trivially obtainable since that specific child-trie slot was never populated by ordinary state on most destination chains.
4. Calls `HandlerV2.handleGetRequestTimeouts(host, message)`. The check `meta.sender == address(0)` passes (still non-zero), the non-membership proof verifies, and `host.dispatchTimeOut(GetRequestTimeout(...), meta, commitment)` executes: `onGetTimeout` fires on the app a second time, and `meta.fee` is refunded a second time to `meta.sender` [3](#0-2) [7](#0-6) .

Result: the same GET request has both `onGetResponse` and `onGetTimeout` executed, and its relayer fee is paid out twice.

### Citations

**File:** modules/ismp/core/src/handlers/timeout.rs (L150-154)
```rust
				// Reject the timeout if a response has already been received for this request
				let response = GetResponse { get: get.clone(), values: Default::default() };
				if host.response_receipt(&response).is_some() {
					Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
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
