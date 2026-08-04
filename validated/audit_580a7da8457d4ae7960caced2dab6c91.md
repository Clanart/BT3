## Analysis

The H‑07 report's core broken invariant: **state is destroyed as if a job succeeded/failed permanently, without checking whether a conflicting terminal state was already reached, and the "recoverable" retry path is inconsistently enforced.** Searching the repo for this exact bug-class shows that Hyperbridge's Substrate ISMP core (`modules/ismp/core/src/handlers/timeout.rs`, `response.rs`, `request.rs`) has already hardened this exact pattern: request/response receipts and commitments are restored on module-callback failure, and — critically — the `Get` timeout path explicitly checks `host.response_receipt(&response).is_some()` and rejects the timeout with `Error::GetResponseAlreadyReceived` if a response was already delivered: [1](#0-0) 

The EVM handler stack does **not** enforce this same invariant. `HandlerV2.handleGetRequestTimeouts` only checks that `_requestCommitments[commitment]` still exists (`meta.sender != address(0)`) and verifies a non-membership proof of the response receipt against the destination chain's trie **at the caller-supplied `message.height`** — it never checks the host's own, current `_responseReceipts[commitment]` state: [2](#0-1) 

Meanwhile, `EvmHost.dispatchIncoming(GetResponse)` pays the relayer's fee straight out of `_requestCommitments[commitment].fee` on success but **never deletes `_requestCommitments[commitment]`** on that success path (contrast with the `PostRequestTimeout`/`GetRequestTimeout` dispatch functions, which explicitly `delete _requestCommitments[commitment]` for replay protection): [3](#0-2) [4](#0-3) 

Because `_requestCommitments[commitment]` is left populated after a GET response has already been delivered and paid, `handleGetRequestTimeouts`'s `meta.sender == address(0)` "known request" check still passes for a request that has *already been fully answered*. Combined with the fact that the non-membership proof is checked only against a caller-chosen historical `message.height` on the destination chain (a height that can legitimately predate when the response was actually recorded there), a relayer can submit a stale-but-valid non-membership proof for a request whose response was delivered later, and drive `EvmHost.dispatchTimeOut(GetRequestTimeout, ...)` to fire `onGetTimeout` on the destination-app module even though `onGetResponse` already executed for the same commitment.

This reproduces the H‑07 pattern of "terminal state deleted/finalized without accounting for the other terminal outcome," but inverted: instead of losing recoverability, the missing guard here allows a request to be driven through **both** terminal callbacks (`onGetResponse` and `onGetTimeout`), a state Hyperbridge's own Substrate reference implementation treats as an explicit invariant violation (`GetResponseAlreadyReceived`).

### Title
Missing "response already received" guard lets GET requests be timed-out after a response was already delivered - (`evm/src/core/HandlerV2.sol`)

### Summary
`handleGetRequestTimeouts` / `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` never check the host's live `_responseReceipts[commitment]` before invoking `onGetTimeout` on the destination module, and `EvmHost.dispatchIncoming(GetResponse)` never clears `_requestCommitments[commitment]` on success. Substrate's `timeout.rs` explicitly forbids this by checking `host.response_receipt(...)` and returning `Error::GetResponseAlreadyReceived`; the EVM path has no equivalent check.

### Finding Description
- On the source chain, `EvmHost.dispatchIncoming(GetResponse, relayer)` marks `_responseReceipts[commitment]`, calls `onGetResponse`, and on success pays the relayer fee out of `_requestCommitments[commitment].fee`, but leaves `_requestCommitments[commitment]` in storage: [3](#0-2) 
- `HandlerV2.handleGetRequestTimeouts` treats "commitment still present" (`meta.sender != address(0)`) as sufficient proof the request is still pending, and only verifies a non-membership proof for the response-receipts slot at an attacker/relayer supplied historical `message.height`: [2](#0-1) 
- Because the commitment is never cleared after a successful `onGetResponse`, and the proof height is not required to be the *current* destination height, a relayer can submit a non-membership proof anchored to a height that predates the actual response delivery, satisfying all handler checks even though the response was already processed.
- `EvmHost.dispatchTimeOut(GetRequestTimeout, ...)` then executes, deleting `_requestCommitments[commitment]` and invoking `onGetTimeout` on the app module: [4](#0-3) 
- This lets the module's `onGetTimeout` fire for a request whose `onGetResponse` already ran — an outcome the Substrate implementation explicitly rejects: [5](#0-4) 

### Impact Explanation
Any `IsmpModule`/`IApp` on EVM that implements both `onGetResponse` and `onGetTimeout` for the same class of GET request and assumes these are mutually exclusive terminal states can be driven into an inconsistent state or duplicate state-mutating action (e.g., an app that finalizes/refunds on `onGetResponse` and separately cleans up/refunds on `onGetTimeout`, per the documented pattern in `docs/content/developers/evm/messaging/get-requests.mdx`). This falls under logic attacks / false state acceptance and potential double-settlement for apps built on this primitive, since the protocol layer itself fails to enforce the one-terminal-outcome invariant it enforces on Substrate.

### Likelihood Explanation
Exploitability is permissionless: any relayer can submit `handleGetRequestTimeouts` with an honestly-generated non-membership proof anchored at an older, still-valid `StateMachineHeight` that the host has previously stored, as long as `challengePeriod` has elapsed for that height and the response was recorded on the destination only after that height. No malicious relayer/prover collusion or forged proof is required — the proof is legitimate for the height it targets; the bug is that the handler doesn't force the proof height to reflect the request's current status.

### Recommendation
- In `EvmHost.dispatchIncoming(GetResponse,...)`, once a response is successfully delivered, mirror the Substrate model and mark the request as terminally resolved (e.g., clear/flag `_requestCommitments[commitment]` or use `_responseReceipts` as the sole source of truth for "already answered").
- In `HandlerV2.handleGetRequestTimeouts` / `EvmHost.dispatchTimeOut(GetRequestTimeout,...)`, explicitly check `host.responseReceipts(commitment).relayer == address(0)` before allowing the timeout dispatch, so a request that has already received a response cannot also be timed out, regardless of the historical proof height supplied.

### Proof of Concept
1. Source chain dispatches a `GetRequest` (`_requestCommitments[commitment]` stored with fee/sender metadata).
2. Destination chain state advances; the response becomes available and is delivered to the source chain via `handleGetResponses` → `EvmHost.dispatchIncoming(GetResponse,...)`, which succeeds, pays the relayer fee, sets `_responseReceipts[commitment]`, but does not clear `_requestCommitments[commitment]`.
3. A relayer (any address, no special privilege) constructs a `GetTimeoutMessage` anchored at an earlier `StateMachineHeight` of the destination chain — one that predates the response being recorded in the destination's response-receipts trie — with a valid non-membership proof for that height.
4. `handleGetRequestTimeouts` checks pass: `meta.sender != address(0)` (commitment still present) and the non-membership proof verifies correctly against the older height's root.
5. `EvmHost.dispatchTimeOut(GetRequestTimeout,...)` executes, invoking `onGetTimeout` on the destination module for a request whose `onGetResponse` has already run — producing the double-terminal-callback condition Substrate's `timeout.rs` explicitly disallows via `GetResponseAlreadyReceived`.

**Note on uncertainty:** I was unable to confirm, from indexed EVM app code, a concrete first-party app (e.g., `IntentGatewayV2.sol`) that implements a fund-moving `onGetTimeout` in a way that is directly exploitable for asset loss/double-spend today — this analysis proves the protocol-level invariant gap in `HandlerV2`/`EvmHost`, and the concrete monetary impact depends on which deployed `IApp` implementations pair `onGetResponse` with a stateful `onGetTimeout`. Confirming a specific double-settlement in a live app would require reviewing every EVM `IApp` implementation's `onGetTimeout`, which is beyond what the indexed snippets could verify with certainty.

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
