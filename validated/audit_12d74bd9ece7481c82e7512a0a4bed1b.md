## Analysis

The reported bug class is: a function returns a state/sentinel indicating "this path is not finished / needs alternate handling," but the caller merges/consumes it as if it were a definitive, already-handled result — causing the same underlying commitment to be actioned twice.

Hyperbridge's ISMP core (Rust/Substrate) enforces exactly this kind of guard for GET requests. In `modules/ismp/core/src/handlers/timeout.rs`, before allowing a GET request timeout to be dispatched, the handler explicitly checks whether a response was already received: [1](#0-0) 

That check has no counterpart in the EVM implementation of the same state machine.

### Title
GET request timeout can be dispatched after its response was already delivered, double-paying the relayer fee - (`evm/src/core/HandlerV2.sol`, `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatchIncoming(GetResponse, address)` pays the relayer fee out of `_requestCommitments[commitment].fee` on successful response delivery but never deletes `_requestCommitments[commitment]`. `HandlerV2.handleGetRequestTimeouts()` — the permissionless entrypoint for processing GET request timeouts — never checks whether a response has already been received (`_responseReceipts[commitment]`) before calling `IHost.dispatchTimeOut(GetRequestTimeout,...)`, which refunds the same still-nonzero fee a second time to `meta.sender`. The Substrate/Rust ISMP core explicitly guards against this ("Reject the timeout if a response has already been received for this request"), but the EVM handler omits the analogous check.

### Finding Description
On the source chain, `EvmHost.dispatch(DispatchGet)` stores the request's fee metadata: [2](#0-1) 

When the GET response is delivered (permissionlessly, via `HandlerV2.handleGetResponses`), `EvmHost.dispatchIncoming(GetResponse, relayer)` pays the fee to the relayer but leaves `_requestCommitments[commitment]` intact: [3](#0-2) 

Note the explicit comment in `handleGetResponses` that timeout is *not* checked here because "it's checked on Hyperbridge": [4](#0-3) 

Separately, `HandlerV2.handleGetRequestTimeouts` is a permissionless entrypoint that only checks (a) challenge period, (b) a remote state commitment exists, (c) the request's `timeout()` has elapsed relative to that remote state's timestamp, (d) the request is "known" (`meta.sender != address(0)`), and (e) a non-membership proof for `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` against that *remote* state root. It never inspects this host's own `_responseReceipts[commitment]`: [5](#0-4) 

If the guard passes, `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` deletes the commitment, calls the app's `onGetTimeout`, and — if that call succeeds — refunds the *same* fee a second time: [6](#0-5) 

By contrast, the Substrate ISMP core explicitly rejects a timeout once a response is already on record before even checking the elapsed-time condition: [7](#0-6) 

The EVM path has no equivalent of `host.response_receipt(&response).is_some()` check. Because `handleGetResponses` intentionally skips the timeout check ("checked on Hyperbridge") and `handleGetRequestTimeouts` never checks whether a response already landed, a request whose `timeout()` has since elapsed relative to some remote state height can be timed out on the source chain *even though its response was already delivered and its fee already paid to the relayer*, since `_requestCommitments[commitment].fee` was never cleared by the successful response delivery.

### Impact Explanation
This is a duplicate/double-settlement of the same GET request fee: once paid to the delivering relayer at response time, and once refunded to `meta.sender` (the fee payer) at timeout time, both movements coming out of the host's `feeToken()` balance for a single logical request. This is direct loss of protocol/host funds through a normal, permissionless flow — no malicious relayer, prover, or admin assumption required, matching the "bridged assets ... relayer rewards ... must move exactly once" bounty pivot.

### Likelihood Explanation
Both entrypoints (`handleGetResponses`, `handleGetRequestTimeouts`) are public and permissionless, callable by any address holding a valid proof for either message type; nothing enforces mutual exclusivity or ordering between them for the same commitment. The only real-world timing requirement is that the GET request's `timeout()` be reachable relative to some remote state commitment's timestamp after a response was already delivered — plausible whenever a GET request is configured with a nonzero timeout and fee, and delivery happens close to (or is even legitimately expected to race) the timeout boundary.

### Recommendation
Mirror the Substrate guard in the EVM handler: before dispatching a GET timeout, check that `IHost.responseReceipts(commitment).relayer == address(0)`; if a response was already received, reject the timeout (revert) instead of proceeding. Additionally, `EvmHost.dispatchIncoming(GetResponse, address)` should clear (or zero out the fee field of) `_requestCommitments[commitment]` after paying the relayer, so that even absent the receipt check, a later timeout attempt cannot read a stale nonzero fee.

### Proof of Concept
1. Attacker (or any user) dispatches `EvmHost.dispatch(DispatchGet{ ..., timeout: T, fee: F })`, funding `F` in the fee token; `_requestCommitments[commitment] = {sender, fee: F}`.
2. A relayer legitimately delivers the response close to expiry via `HandlerV2.handleGetResponses(...)`; `EvmHost.dispatchIncoming(GetResponse, relayer)` runs, pays `F` to `relayer`, sets `_responseReceipts[commitment]`, but leaves `_requestCommitments[commitment].fee == F`.
3. After the request's `timeout()` becomes provably elapsed relative to some remote state commitment already known to the host, anyone submits a `GetTimeoutMessage` with a valid non-membership proof for `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` at that remote height to `HandlerV2.handleGetRequestTimeouts(...)`.
4. The handler's checks all pass (no check against local `_responseReceipts`), so it calls `EvmHost.dispatchTimeOut(GetRequestTimeout, meta, commitment)`; the app's `onGetTimeout` succeeds (typical for cleanup-only handlers), and the host refunds `F` a second time to `meta.sender`.
5. Total fee-token outflow from the host for this single request is `2F` instead of `F`.

### Citations

**File:** modules/ismp/core/src/handlers/timeout.rs (L139-164)
```rust
		TimeoutMessage::Get { requests } => {
			let wrapped: Vec<Request> = requests.iter().cloned().map(Request::Get).collect();
			dedup_requests::<H>(&wrapped)?;

			for get in &requests {
				let commitment = hash_request::<H>(&Request::Get(get.clone()));
				// if we have a commitment, it came from us
				if host.request_commitment(commitment).is_err() {
					Err(Error::UnknownRequest { meta: get.into() })?
				}

				// Reject the timeout if a response has already been received for this request
				let response = GetResponse { get: get.clone(), values: Default::default() };
				if host.response_receipt(&response).is_some() {
					Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
				}

				// Ensure the get timeout has elapsed on the host
				if !get.timed_out(host.timestamp()) {
					Err(Error::RequestTimeoutNotElapsed {
						meta: get.into(),
						timeout_timestamp: get.timeout(),
						state_machine_time: host.timestamp(),
					})?
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

**File:** evm/src/core/EvmHost.sol (L987-1001)
```text
        uint64 timeoutTimestamp = get.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(get.timeout);
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            timeoutTimestamp: timeoutTimestamp,
            keys: get.keys,
            height: get.height,
            context: get.context
        });

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
