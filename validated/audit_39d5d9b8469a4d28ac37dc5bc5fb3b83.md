## Title
GET request timeout dispatch on EVM does not check for an already-delivered response, enabling double dispatch (`onGetResponse` + `onGetTimeout`) for the same request - (File: `evm/src/core/HandlerV2.sol`, `evm/src/core/EvmHost.sol`)

### Summary
The Substrate ISMP core explicitly guards against timing out a `GetRequest` that has already received a response by checking the local `response_receipt` before dispatching `on_timeout`. The equivalent EVM path (`HandlerV2.handleGetRequestTimeouts` → `EvmHost.dispatchTimeOut(GetRequestTimeout,...)`) has no such check. It only verifies a historical *non-membership* proof and that a request-fee-metadata entry exists — an entry that is never cleared after a successful response delivery. This lets a permissionless caller dispatch a stale timeout for a `GetRequest` after its response has already been delivered, invoking `onGetTimeout` on the destination module even though `onGetResponse` already ran for the same commitment.

### Finding Description
In the Rust ISMP core, `handle` for `TimeoutMessage::Get` explicitly rejects a timeout if a response was already received: [1](#0-0) 

This invariant is exercised by dedicated tests confirming that a Get timeout must fail once a response receipt exists: [2](#0-1) 

The EVM equivalent, `handleGetRequestTimeouts`, never performs this check. It only validates the challenge period, a non-membership proof at a chosen historical height, and that fee metadata (`meta.sender`) is non-zero: [3](#0-2) 

`meta.sender` comes from `_requestCommitments[commitment]`, which is populated when the request is dispatched and is **never cleared** after a successful GET response is delivered. Compare `dispatchIncoming(GetResponse ...)`, which pays the relayer fee out of `_requestCommitments[commitment].fee` but does not delete the entry (unlike the timeout dispatch functions, which explicitly `delete _requestCommitments[commitment]` for "replay protection"): [4](#0-3) [5](#0-4) 

Because `_requestCommitments[commitment]` is left populated after a response is delivered, `meta.sender != address(0)` remains true forever, so `handleGetRequestTimeouts`'s only "already answered" gate never fires. The non-membership proof itself only proves the response receipt was absent at a specific *historical* state height — it says nothing about the request's current state. The code never re-checks the live `_responseReceipts[commitment]` mapping before calling `dispatchTimeOut`: [6](#0-5) 

As a result, once a GET response is legitimately delivered via `handleGetResponses` (which sets `_responseReceipts[commitment]` and invokes `onGetResponse`): [7](#0-6) 

...an attacker (any permissionless caller — both handler functions state "Access: Permissionless") can still submit a `GetTimeoutMessage` built from a non-membership proof anchored at a state height that predates the response delivery, and successfully invoke `onGetTimeout` on the same destination module for the same commitment.

### Impact Explanation
This is a duplicate/double-settlement of a single cross-chain request: the destination application module receives **both** `onGetResponse` (successful answer) and `onGetTimeout` (request-failed/refund) callbacks for the same `GetRequest` commitment. Any `IApp` implementation that assumes exclusivity between these two lifecycle events (e.g., an intent/escrow contract that releases funds on `onGetResponse` and refunds/unlocks the same escrow on `onGetTimeout`) can be driven into double-spend / double-refund / broken accounting state, i.e. unauthorized execution and duplicate settlement of a single bridged request — matching the bounty's explicit "double-claim/double-settlement" and "unauthorized transaction or execution" categories.

### Likelihood Explanation
No privileged role, relayer collusion, or leaked key is required. `handlePostRequestTimeouts`/`handleGetRequestTimeouts`/`handleGetResponses` are all explicitly permissionless entry points, and old `StateMachineHeight` commitments remain queryable in storage indefinitely (`_stateCommitments`/`_stateCommitmentsUpdateTime` are plain mappings, never pruned), so a valid non-membership proof from a height prior to response delivery remains submittable at any later time. The only missing piece an attacker needs is a legitimately obtainable historical proof, which is a normal artifact of protocol operation, not an adversarial capability.

### Recommendation
Mirror the Rust core's guard in the EVM handler/host: before invoking `dispatchTimeOut` for a GET request, check that `_responseReceipts[commitment]` is unset (i.e., no response was ever delivered), and revert with a `GetResponseAlreadyReceived`-style error if it is. Additionally, delete/zero `_requestCommitments[commitment]` after a GET response is successfully dispatched in `dispatchIncoming(GetResponse,...)`, consistent with the replay-protection pattern already used in both `dispatchTimeOut` overloads.

### Proof of Concept
1. Application dispatches a `GetRequest` with `timeout` set far in the future; `EvmHost` stores `_requestCommitments[commitment]` with fee metadata.
2. Consensus/state proof for the destination chain is submitted at height `H1`, at which no response receipt for `commitment` exists on the source EVM host — an observer can capture this height and later produce a non-membership proof for `RESPONSE_RECEIPTS_STORAGE_PREFIX + commitment` against `H1`'s state root.
3. Relayer delivers the legitimate `GetResponseMessage` via `handleGetResponses`; `_responseReceipts[commitment]` is set and `onGetResponse` fires on the destination module (`evm/src/core/HandlerV2.sol:217-247`, `evm/src/core/EvmHost.sol:824-847`). Note `_requestCommitments[commitment]` is left untouched.
4. Attacker (any address) now calls `handleGetRequestTimeouts` with a `GetTimeoutMessage` whose `height` is `H1` and whose `proof` is the non-membership proof captured in step 2. `meta.sender` from `_requestCommitments[commitment]` is still non-zero, so the `UnknownMessage` check passes; the non-membership proof against `H1` verifies successfully because it correctly reflects the pre-response state (`evm/src/core/HandlerV2.sol:303-320`).
5. `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` executes, invoking `onGetTimeout` on the same destination module that already received `onGetResponse` for the same commitment (`evm/src/core/EvmHost.sol:856-877`) — no check against `_responseReceipts` prevents this.

### Citations

**File:** modules/ismp/core/src/handlers/timeout.rs (L150-154)
```rust
				// Reject the timeout if a response has already been received for this request
				let response = GetResponse { get: get.clone(), values: Default::default() };
				if host.response_receipt(&response).is_some() {
					Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
				}
```

**File:** modules/pallets/testsuite/src/tests/pallet_ismp.rs (L230-256)
```rust
#[test]
fn should_reject_get_timeout_batch_when_any_request_has_response() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let host = Ismp::default();
		setup_mock_client::<_, Test>(&host);
		let id = StateMachineId {
			state_id: StateMachine::Evm(11155111),
			consensus_state_id: MOCK_CONSENSUS_STATE_ID,
		};
		host.store_challenge_period(id, 0).unwrap();

		let requests = (0..2)
			.into_iter()
			.map(|i| {
				host.dispatch_request(
					DispatchRequest::Get(DispatchGet {
						dest: StateMachine::Evm(1),
						from: vec![0u8; 32],
						keys: vec![vec![1u8; 32], vec![1u8; 32]],
						context: Default::default(),
						height: 2,
						timeout: 1000,
					}),
					FeeMetadata { payer: [0u8; 32].into(), fee: Default::default() },
				)
				.unwrap();
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
