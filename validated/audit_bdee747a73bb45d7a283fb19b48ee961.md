Based on direct code evidence, I found a concrete asymmetry between the EVM and Substrate ISMP timeout handlers for `GetRequest`s that the IntentGatewayV2 cancellation flow directly exercises.

### Title
GetRequest timeout handling on EVM treats `timeout == 0` ("never expires") as already timed-out and never checks for an existing response, enabling premature/duplicate `onGetTimeout` dispatch - ([File: evm/src/core/HandlerV2.sol])

### Summary
`ExtrinsicIntents._cancelFromSource` (and the Tron `IntentGatewayV2._cancelFromSource`) dispatch a `DispatchGet` request with `timeout: 0`, following the same convention used for `DispatchPost` in `EvmHost.dispatch(DispatchPost)`, where `post.timeout == 0` is explicitly translated to `timeoutTimestamp = 0` meaning "never times out" [1](#0-0) . The corresponding request construction in the intent gateway reuses the same zero-timeout idiom [2](#0-1) .

However, `HandlerV2.handleGetRequestTimeouts` gates on `if (request.timeout() > state.timestamp) revert MessageNotTimedOut();` [3](#0-2) . For a request whose `timeout()` is `0` (the "never expires" sentinel), `0 > state.timestamp` is false for any non-negative timestamp, so the "not timed out yet" guard never fires — a zero-timeout request is treated as *immediately eligible* for timeout processing, the opposite of the intended "never times out" semantics.

### Finding Description
Compounding this, `EvmHost.dispatchTimeOut(GetRequestTimeout ...)` performs no check against `_responseReceipts[commitment]` before invoking `onGetTimeout` on the app — it only deletes `_requestCommitments[commitment]` as replay protection [4](#0-3) . Contrast this with the Substrate `pallet-ismp` timeout handler, which explicitly rejects a Get timeout once a response has already been recorded (`GetResponseAlreadyReceived`), and this exact guard is unit-tested [5](#0-4) [6](#0-5) . The EVM `HandlerV2` path has no equivalent local check against `host.responseReceipts(commitment)` — it only relies on a non-membership *proof* against a state commitment at `message.height`, which is a state root the caller can pick from any previously stored `StateMachineHeight`, including one recorded before a legitimate response was resolved [7](#0-6) .

Because `_requestCommitments[commitment]` is *not* deleted when a `GetResponse` is legitimately delivered via `dispatchIncoming(GetResponse ...)` (only `_responseReceipts[commitment]` is set, and the fee is paid without clearing the request record) [8](#0-7) , the request stays "known" and eligible for a subsequent timeout submission built against an older, still-stored state commitment where the response did not yet exist.

### Impact Explanation
Any registered ISMP app dispatching a `GetRequest` with `timeout: 0` (the exact idiom used by `IntentGatewayV2._cancelFromSource` / `ExtrinsicIntents._cancelFromSource`) is exposed to `onGetTimeout` being triggered essentially on demand by an unprivileged caller, independent of and potentially concurrent with the legitimate `onGetResponse` delivery. This breaks the "the response and the timeout for one request are mutually exclusive, one-time outcomes" invariant that the Substrate side explicitly enforces. For an app whose `onGetTimeout`/`onGetResponse` pair both drive escrow release (as `withdraw()` does for `RefundEscrow`/`RedeemEscrow` in the intent gateway), this class of bug is what enables duplicate settlement/fund loss if both hooks are wired to move funds for the same commitment.

### Likelihood Explanation
Triggering the vulnerable path only requires calling the permissionless `handleGetRequestTimeouts` entrypoint with a valid state-machine proof drawn from any previously accepted, still-stored `StateMachineHeight` for the destination — no relayer, prover, or admin privilege is needed, and the zero-timeout condition is met immediately upon dispatch rather than after a genuine waiting period.

### Recommendation
Mirror the Substrate guard: reject `GetRequestTimeoutMessage` processing when `host.responseReceipts(commitment).relayer != address(0)`, and treat `timeoutTimestamp == 0` as "never times out" (skip/revert rather than treat as pre-expired) in `handleGetRequestTimeouts`, matching the `DispatchPost`/`DispatchGet` dispatch-side semantics.

### Proof of Concept
Not independently executable from the indexed code alone — I could not confirm from available context whether `IntentGatewayV2`/`ExtrinsicIntents` actually implements `onGetTimeout` with a fund-moving effect (only `onGetResponse`'s `withdraw(body, true)` call was directly observed in the retrieved snippets). This is a material gap: without a confirmed `onGetTimeout` override that also transfers escrowed funds, the described defect is a protocol-level correctness bug (premature/duplicate timeout dispatch) rather than a proven fund-loss path in this specific app. A full verification would require reading the complete `onGetTimeout` implementation (if any) across `evm/src/apps/intentsv2/*.sol` and `evm/tron/contracts/apps/IntentGatewayV2.sol`, which was not available within the current tool budget — I recommend a Devin session with full repository access to confirm or rule out the fund-impact chain before treating this as exploitable beyond the confirmed handler-logic defect.

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

**File:** evm/src/core/EvmHost.sol (L934-935)
```text
        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
```

**File:** evm/src/apps/intentsv2/ExtrinsicIntents.sol (L207-215)
```text
        DispatchGet memory request = DispatchGet({
            dest: order.destination,
            keys: keys,
            timeout: 0,
            height: options.height,
            fee: options.relayerFee,
            context: context,
            payer: msg.sender
        });
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

**File:** evm/src/core/HandlerV2.sol (L293-306)
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
```

**File:** modules/ismp/core/src/handlers/timeout.rs (L150-154)
```rust
				// Reject the timeout if a response has already been received for this request
				let response = GetResponse { get: get.clone(), values: Default::default() };
				if host.response_receipt(&response).is_some() {
					Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
				}
```

**File:** modules/ismp/testsuite/src/lib.rs (L354-375)
```rust
/// Reject a GET timeout when the request has already received a response. The request's timeout
/// hasn't elapsed either, so without the response-receipt guard the handler would have failed
/// with `RequestTimeoutNotElapsed` — proving the response check runs first.
pub fn get_response_already_received_check<H>(host: &H) -> Result<(), &'static str>
where
	H: IsmpHost + IsmpDispatcher,
	H::Account: From<[u8; 32]>,
	H::Balance: From<u32> + Default,
{
	let intermediate_state = setup_mock_client(host);
	let get =
		dispatch_get_request(host, &intermediate_state, host.timestamp().as_secs() + 1_000_000);

	let response = GetResponse { get: get.clone(), values: Default::default() };
	host.store_response_receipt(&response, &vec![0u8; 32]).unwrap();

	let timeout_message = Message::Timeout(TimeoutMessage::Get { requests: vec![get] });

	let res = handle_incoming_message(host, timeout_message).map_err(|e| e.downcast().unwrap());
	assert!(matches!(res, Err(Error::GetResponseAlreadyReceived { .. })));
	Ok(())
}
```
