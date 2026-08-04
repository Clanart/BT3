## Analysis

The C4 report's core broken invariant: a payout/settlement path (`_payExecutionGas`) can be reached a second time for the same logical request because the guard that should block a duplicate payout (`initialGas`/`userFeeInfo`) is not properly isolated per invocation, allowing double-claim/no-charge. The Hyperbridge analog is a structurally identical gap in the GET-request fee lifecycle on the EVM host: the same escrowed relayer fee can be paid out **twice** — once through response delivery, once through timeout refund — because the EVM handler is missing a guard that the equivalent Substrate handler explicitly implements.

### Title
Double payment of GET request relayer fee via missing response-receipt check in `handleGetRequestTimeouts` - (File: `evm/src/core/HandlerV2.sol`)

### Summary
`HandlerV2.handleGetRequestTimeouts` refunds the escrowed relayer fee for a GET request without first checking whether that request's response has already been delivered (and its fee already paid) via `handleGetResponses`. The Rust core `pallet-ismp` timeout handler has an explicit safeguard for this exact scenario, but the EVM handler lacks it.

### Finding Description
In `dispatchIncoming(GetResponse ...)`, upon successful delivery of a GET response, `EvmHost` pays the escrowed fee to the relayer but never deletes `_requestCommitments[commitment]`: [1](#0-0) 

Separately, `HandlerV2.handleGetRequestTimeouts` only validates that the timeout timestamp has elapsed and that a non-membership proof (against `RESPONSE_RECEIPTS_STORAGE_PREFIX`) verifies for the proven height — it never checks the *local* host's `_responseReceipts` mapping to see if the response was already received on this chain: [2](#0-1) 

It then calls `host.dispatchTimeOut(GetRequestTimeout(...), meta, commitment)`, which deletes `_requestCommitments[commitment]` and refunds `meta.fee` to `meta.sender`: [3](#0-2) 

Because `_requestCommitments[commitment]` is never cleared when the GET response path pays the relayer, the same fee metadata (with the same non-zero `fee`) is still readable and refundable by the timeout path afterward. This is precisely the class of bug in the C4 report: a value that should be a one-time-use gate against a duplicate settlement (`initialGas`/`userFeeInfo` there, `_requestCommitments[commitment]` here) is not invalidated across the two competing completion paths, enabling the payout logic to run twice for one logical request.

Contrast this with the Rust core module, which explicitly guards against this exact race: [4](#0-3) 
```
// Reject the timeout if a response has already been received for this request
if host.response_receipt(&response).is_some() {
    Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
}
```
No equivalent check (`host.responseReceipts(commitment) == address(0)`) exists in `HandlerV2.handleGetRequestTimeouts` for EVM. The GET response path also explicitly documents that it does not check timeouts locally ("don't check for timeouts because it's checked on Hyperbridge"), meaning a response can legitimately land close to or even conceptually after the local timeout window opens, widening the exploitable window: [5](#0-4) 

### Impact Explanation
This causes duplicate settlement of protocol fee-token funds held by `EvmHost`: the relayer that delivered the GET response is paid once via `dispatchIncoming(GetResponse)`, and then the same fee amount is paid again — to `meta.sender` (the fee-token payer) — via `dispatchTimeOut(GetRequestTimeout)`, draining the host's fee-token balance beyond what was actually escrowed for that request. This is unauthorized double-claim/double-settlement of escrowed funds, matching the bounty's explicit "double-claim/double-settlement" and "loss of funds" impact categories. It also triggers `IApp.onGetTimeout` for an application whose request was already successfully answered, which can corrupt application-level state that assumes a request resolves exactly once (e.g., re-processing a "timed out" query the app already handled as fulfilled).

### Likelihood Explanation
The call is fully permissionless — anyone can submit a `GetTimeoutMessage` to `IHandler.handleGetRequestTimeouts()` once a valid non-membership proof and elapsed timeout can be produced, without needing to be the original relayer, a privileged actor, or a malicious peer/prover. Because `handleGetResponses` intentionally omits any timeout check, the two code paths (response delivery and timeout) are not mutually exclusive at the EVM-host level, and nothing on this contract stops both from executing for the same commitment as long as each individual proof requirement is separately satisfiable.

### Recommendation
In `EvmHost.dispatchTimeOut(GetRequestTimeout ...)` (and ideally earlier, in `HandlerV2.handleGetRequestTimeouts`), check that `_responseReceipts[commitment]` (equivalently `responseReceipts(commitment).relayer == address(0)`) before proceeding, mirroring the Rust core module's `GetResponseAlreadyReceived` guard. Additionally, delete `_requestCommitments[commitment]` inside `dispatchIncoming(GetResponse ...)` after fee payment so stale fee metadata cannot be reused by any other code path.

### Proof of Concept
1. App on the source EVM host dispatches a `DispatchGet` with `fee = F`, stored in `_requestCommitments[commitment]`.
2. A relayer submits a valid `GetResponseMessage` via `HandlerV2.handleGetResponses` → `EvmHost.dispatchIncoming(GetResponse, relayer)` succeeds; `relayer` is paid `F` in fee token. `_requestCommitments[commitment]` is left untouched (still holds `fee = F`, `sender = payer`).
3. Once `block.timestamp`/state timestamp exceeds `request.timeout()`, any permissionless caller assembles a `GetTimeoutMessage` with a valid non-membership proof for the relevant height/key and calls `IHandler.handleGetRequestTimeouts`.
4. `handleGetRequestTimeouts` finds `meta.sender != address(0)` (commitment still present) and, without checking `_responseReceipts`, calls `host.dispatchTimeOut(GetRequestTimeout, meta, commitment)`.
5. `dispatchTimeOut` deletes `_requestCommitments[commitment]` and refunds `F` again to `payer`, even though `F` was already paid out to the relayer in step 2 — the host has now paid `2F` in fee token for a single `F`-funded request.

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

**File:** evm/src/core/HandlerV2.sol (L226-234)
```text
        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // don't check for timeouts because it's checked on Hyperbridge

            // known request? also serves as source check
            FeeMetadata memory meta = host.requestCommitments(leaf.response.request.hash());
            if (meta.sender == address(0)) revert UnknownMessage();
            leaves[i] = MerkleMountainRange.Leaf(leaf.index, leaf.response.hash());
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

**File:** modules/ismp/core/src/handlers/timeout.rs (L150-154)
```rust
				// Reject the timeout if a response has already been received for this request
				let response = GetResponse { get: get.clone(), values: Default::default() };
				if host.response_receipt(&response).is_some() {
					Err(Error::GetResponseAlreadyReceived { meta: get.into() })?
				}
```
