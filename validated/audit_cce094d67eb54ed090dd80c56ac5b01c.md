Based on my investigation, I confirmed the vulnerability in `HandlerV2.sol`'s POST/GET timeout handlers: `message.height` (a caller-supplied `StateMachineHeight`) is never checked against `request.dest` (for POST) or the expected source/response location (for GET) before being used to fetch a `StateCommitment` and verify a non-membership proof against it.

### Title
Timeout handlers accept a non-membership proof from an arbitrary state machine instead of the request's actual destination, enabling false timeout acceptance and duplicate relayer-fee refunds - (File: evm/src/core/HandlerV2.sol)

### Summary
`handlePostRequestTimeouts` and `handleGetRequestTimeouts` in `evm/src/core/HandlerV2.sol` fetch a `StateCommitment` using a caller-controlled `message.height` (a `StateMachineHeight{stateMachineId, height}` pair) and verify a non-membership proof of the request/response receipt against that commitment's root, but never assert that `message.height.stateMachineId == request.dest` (POST) or the state machine that should hold the response receipt (GET). [1](#0-0) [2](#0-1) 

This is the exact analog of the H-1 root cause: a state-transition path ("close/settle") is executed while skipping a binding check that other equivalent paths perform, letting the protocol act on the wrong/incomplete state and release funds it shouldn't.

### Finding Description
The Rust reference implementation of the same timeout logic explicitly binds the supplied height to the request's destination before trusting the non-membership proof:
```rust
if dest_chain != timeout_proof.height.id.state_id && !allow_proxy {
    Err(Error::RequestProxyProhibited { meta: post.into() })?
}
``` [3](#0-2) 

The EVM `HandlerV2.sol` implementation of the equivalent flow omits this check entirely. It only validates:
1. `challengePeriod` has elapsed for `message.height`,
2. a `StateCommitment` exists at `message.height`,
3. `request.timeout() > state.timestamp` (timeout has elapsed, relative to whatever chain `message.height` points to),
4. a non-membership proof of `REQUEST_RECEIPTS_STORAGE_PREFIX + commitment` (POST) or `RESPONSE_RECEIPTS_STORAGE_PREFIX + commitment` (GET) against `state.stateRoot`. [4](#0-3) 

None of these checks constrain `message.height.stateMachineId` to `request.dest`. Since Hyperbridge tracks state commitments for many distinct state machines (`host.stateMachineCommitment` is keyed by an arbitrary `StateMachineHeight`), a caller can supply the height of a completely unrelated, already-finalized chain that Hyperbridge has a commitment for. Because the receipt key is `REQUEST_RECEIPTS_STORAGE_PREFIX/RESPONSE_RECEIPTS_STORAGE_PREFIX + commitment` — a globally unique hash tied to the real destination — that key will almost certainly be absent from the unrelated chain's trie, so the "non-membership" proof trivially succeeds even though the request was actually delivered and processed on the real `request.dest`. The docs confirm this handler is explicitly **permissionless**: "Access: Permissionless (can be called by anyone)". [5](#0-4) 

Once this false non-membership proof is accepted, `host.dispatchTimeOut(...)` is invoked, which deletes the request commitment and calls the source module's `onPostRequestTimeout`/`onGetTimeout`, refunding the relayer fee to the payer via `IERC20(feeToken()).safeTransfer(meta.sender, meta.fee)`. [6](#0-5) 

This directly parallels the H-1 root cause: a state-finalizing action (`closeThePositionInSynthetix`/here, `dispatchTimeOut`) proceeds without validating/binding to the correct counterpart state (global liquidation bookkeeping/here, the correct destination chain identity), letting the protocol treat an unrelated or stale state fact as authoritative for a live commitment.

### Impact Explanation
This breaks the core "false proof/state acceptance" invariant explicitly listed in the bounty scope: the timeout path can be triggered against a request that was actually delivered and successfully executed on its true destination, because the proof is bound to the wrong chain's root. Consequences:
- **False state acceptance**: a request marked as "delivered" on the real destination can still be timed out on the source, because the non-membership check ran against an unrelated chain.
- **Fund loss / double settlement**: for POST requests, this triggers `onPostRequestTimeout` (application-level rollback logic, e.g. unlocking bridged funds on source) *in addition to* the funds already delivered on the real destination — a double-spend of the escrowed value, plus an illegitimate relayer-fee refund to `meta.sender` even though the message was actually delivered (the delivering relayer should have been paid instead).
- Matches the bounty's explicit "false proof/state acceptance" and "replay/double-claim/double-settlement" categories, and is reachable by any unprivileged, permissionless caller with no admin/relayer/prover collusion required — only knowledge of an arbitrary already-committed `StateMachineHeight` for some other chain Hyperbridge tracks.

### Likelihood Explanation
High feasibility for an attacker who is simply an unprivileged observer:
- `handlePostRequestTimeouts`/`handleGetRequestTimeouts` are explicitly permissionless.
- `message.height` is fully attacker-supplied; any previously-stored `StateCommitment` for any state machine works, and Hyperbridge continuously accumulates such commitments for every chain it bridges.
- Building the non-membership proof against an unrelated chain's root is a normal Merkle/Trie non-membership proof of a key that is virtually guaranteed not to exist there — no cryptographic break needed, no cooperation from a relayer/prover/admin required.
- The only real precondition is that the targeted request has actually timed out per its own `timeoutTimestamp` relative to `state.timestamp` of the substituted chain — trivially satisfiable by choosing an old-enough height with a large timestamp.

### Recommendation
Add an explicit binding check mirroring the Rust `timeout.rs` logic before trusting any non-membership proof:
```solidity
if (!request.dest.equals(<expected state machine bytes for message.height.stateMachineId>)) revert InvalidMessageDestination();
```
Concretely, before verifying the trie proof in both `handlePostRequestTimeouts` and `handleGetRequestTimeouts`, assert that `message.height.stateMachineId` corresponds exactly to `request.dest` (POST) or the state machine expected to hold the response receipt for that GET request. This should be enforced unconditionally (or, if request-proxying is a supported feature on EVM as it is on Substrate, gated the same way `is_allowed_proxy`/`check_state_machine_client` gate it in `modules/ismp/core/src/handlers/timeout.rs`), so an attacker cannot substitute an unrelated chain's committed state root.

### Proof of Concept
1. Attacker dispatches (or observes) a `PostRequest` from chain A to chain B via `EvmHost.dispatch`, creating `_requestCommitments[commitment]` on A. [7](#0-6) 
2. The request is legitimately delivered and processed on chain B (receipt exists there).
3. Once `request.timeoutTimestamp` has elapsed relative to *some* chain C's committed timestamp (any chain Hyperbridge has a `StateCommitment` for, not B), the attacker builds a `PostRequestTimeoutMessage` with `message.height = {stateMachineId: C, height: h}` and a genuine non-membership proof that `REQUEST_RECEIPTS_STORAGE_PREFIX + commitment` is absent from chain C's trie (trivially true, since the receipt was written on chain B, not C).
4. Attacker calls `handlePostRequestTimeouts(host, message)` on chain A. All existing checks pass (`challengePeriod` on C's commitment, `state.stateRoot != 0`, `request.timeout() > state.timestamp` from C, non-membership on C's root, known commitment on A) because none of them verify `C == request.dest (B)`.
5. `host.dispatchTimeOut` executes, deleting the request commitment, invoking `onPostRequestTimeout` on the source app (potentially reverting escrowed state) and refunding `meta.fee` to `meta.sender` — even though the message was already delivered and paid for on chain B. [8](#0-7) 

I was unable to fully verify from the indexed code whether `IsmpModule::onPostRequestTimeout` implementations in the shipped `IntentGatewayV2`/other apps perform their own destination re-validation that would neutralize this at the application layer (the index does not show every app's timeout callback); a Devin session with full repo access should confirm whether any app-level guard closes this gap before treating it as fully exploitable end-to-end for a specific app.

### Citations

**File:** evm/src/core/HandlerV2.sol (L254-286)
```text
    function handlePostRequestTimeouts(IHost host, PostRequestTimeoutMessage calldata message)
        external
        notFrozen(host)
    {
        uint256 delay = block.timestamp - host.stateMachineCommitmentUpdateTime(message.height);
        uint256 challengePeriod = host.challengePeriod();
        if (challengePeriod != 0 && challengePeriod > delay) revert ChallengePeriodNotElapsed();

        // fetch the state commitment
        StateCommitment memory state = host.stateMachineCommitment(message.height);
        if (state.stateRoot == bytes32(0)) revert StateCommitmentNotFound();
        uint256 timeoutsLength = message.timeouts.length;

        for (uint256 i = 0; i < timeoutsLength; ++i) {
            PostRequest memory request = message.timeouts[i];
            // timed-out?
            if (request.timeout() > state.timestamp) revert MessageNotTimedOut();

            // known request? also serves as source check
            bytes32 requestCommitment = request.hash();
            FeeMetadata memory meta = host.requestCommitments(requestCommitment);
            if (meta.sender == address(0)) revert UnknownMessage();

            bytes[] memory keys = new bytes[](1);
            keys[0] = bytes.concat(REQUEST_RECEIPTS_STORAGE_PREFIX, requestCommitment);

            // verify state trie non-membership proofs
            PolkadotTrie.StorageValue memory entry = PolkadotTrie.VerifyProof(state.stateRoot, message.proof, keys)[0];
            if (entry.value.length != 0) revert InvalidProof();

            host.dispatchTimeOut(PostRequestTimeout(request, _msgSender()), meta, requestCommitment);
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

**File:** modules/ismp/core/src/handlers/timeout.rs (L56-67)
```rust
			for post in &requests {
				let dest_chain = post.dest;

				// in order to allow proxies, the host must configure the given state machine
				// as it's proxy and must not have a state machine client for the destination chain
				let allow_proxy = host.is_allowed_proxy(&timeout_proof.height.id.state_id) &&
					check_state_machine_client(dest_chain);

				// check if the timeout is allowed to be proxied
				if dest_chain != timeout_proof.height.id.state_id && !allow_proxy {
					Err(Error::RequestProxyProhibited { meta: post.into() })?
				}
```

**File:** docs/content/developers/evm/api/ihandler.mdx (L121-153)
```text
### handlePostRequestTimeouts()

Processes timed-out POST requests and triggers refunds.

```solidity lineNumbers
function handlePostRequestTimeouts(
    IHost host,
    PostRequestTimeoutMessage calldata message
) external
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `host` | `IHost` | The IHost contract |
| `message` | `PostRequestTimeoutMessage` | Struct containing timeout proof and requests |

**Access:** Permissionless (can be called by anyone)

**Process:**
1. Verifies timeout proof
2. For each request:
   - Validates timeout timestamp has passed
   - Calls `onPostRequestTimeout()` on source application
   - Refunds relayer fee to payer (only if callback succeeds)

**Important:**
- Application timeout callback is called **before** refund
- If callback reverts, no refund occurs
- Timeout can be resubmitted until callback succeeds

**Reverts:**
- `MessageNotTimedOut()` - Timeout period not elapsed
- `UnknownMessage()` - Request not found
```

**File:** evm/src/core/EvmHost.sol (L879-906)
```text
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

**File:** evm/src/core/EvmHost.sol (L921-959)
```text
    function dispatch(DispatchPost memory post) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                post.fee, path, address(this), block.timestamp
            );
        } else if (post.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), post.fee);
        }

        // adjust the timeout
        uint64 timeoutTimestamp = post.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(post.timeout);
        PostRequest memory request = PostRequest({
            source: host(),
            dest: post.dest,
            nonce: uint64(_nextNonce()),
            from: abi.encodePacked(_msgSender()),
            to: post.to,
            timeoutTimestamp: timeoutTimestamp,
            body: post.body
        });

        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
        emit PostRequestEvent({
            source: string(request.source),
            dest: string(request.dest),
            from: _msgSender(),
            to: abi.encodePacked(request.to),
            nonce: request.nonce,
            timeoutTimestamp: request.timeoutTimestamp,
            body: request.body,
            fee: post.fee
        });
    }
```
