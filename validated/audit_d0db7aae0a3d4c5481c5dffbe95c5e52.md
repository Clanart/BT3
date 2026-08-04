### Title
Duplicate relayer-fee payout for GET requests via stale `_requestCommitments` after a successful `GetResponse` dispatch - (File: `evm/src/core/EvmHost.sol`)

### Summary
The external report's core broken invariant is: a "did the callback complete correctly?" signal (an unsafe try/catch / low-level call success flag) is used to gate an irreversible, one-time state transition, and the code fails to make that transition atomic and idempotent, allowing money to move more than once or not exactly once. The local Hyperbridge analog is in `EvmHost.dispatchIncoming(GetResponse, relayer)`: on a successful callback it pays the relayer the request's fee but never clears the `FeeMetadata` in `_requestCommitments`, leaving the same commitment payable again through the separate GET-timeout path (`dispatchTimeOut` / `handleGetRequestTimeouts`), which is gated only by a merkle non-membership proof at an attacker-chosen historical height rather than the current state.

### Finding Description
`dispatchIncoming(GetResponse memory response, address relayer)` pays the fee and emits `GetRequestHandled` on success, but does not delete `_requestCommitments[commitment]`: [1](#0-0) 

Compare this to the timeout path, `dispatchTimeOut(GetRequestTimeout, meta, commitment)`, which independently reads/pays from the same `FeeMetadata` and only then deletes it: [2](#0-1) 

The gate for reaching the timeout path is `HandlerV2.handleGetRequestTimeouts`, which accepts *any* previously stored state-machine height (`host.stateMachineCommitment(message.height)`, not necessarily the latest) and verifies a state-trie **non-membership** proof of the response receipt at that height: [3](#0-2) 

`_stateCommitments` in `EvmHost` is keyed per-height and old entries are never purged, so a commitment for a height that predates the point at which the GET response was actually delivered on Hyperbridge/relayed to this host remains queryable. The comment in `handleGetResponses` explicitly notes timeout-checking is *not* performed locally ("don't check for timeouts because it's checked on Hyperbridge"), meaning the EVM-side timeout path trusts only the non-membership proof, not any global ordering guarantee tied to whether a response for that exact commitment was already paid out on this host.

Because `_requestCommitments[commitment]` (the fee metadata) is left intact after a successful `GetResponse` fee payout, the same commitment can later satisfy `handleGetRequestTimeouts`'s `meta.sender == address(0)` liveness check and its non-membership proof at an older height, driving a second, independent `safeTransfer` of `meta.fee` via `dispatchTimeOut`.

### Impact Explanation
This is a duplicate/double-settlement of protocol fee funds — the exact "duplicate-claim / double-settlement" category called out in the bounty scope. A relayer (or a party colluding with/acting as the relayer) can collect the fee twice for the same GET request: once through legitimate response delivery, and again by submitting a valid — but stale — non-membership timeout proof, since nothing invalidates the `FeeMetadata` entry after the response-triggered payout.

### Likelihood Explanation
No admin, governance, or malicious-relayer privilege is required beyond being a relayer submitting proofs, which is a permissionless, unprivileged role (the bounty explicitly excludes "malicious relayer" as an *assumption* to reject, but exploiting a bug via a normal relayer's own permissionless transaction submission is in scope; the attacker never needs a compromised key, only their own funds/relaying rights). The only requirement is presenting a merkle proof against an already-stored older state-machine height, which is routine — old heights are retained and no code path re-validates that a GET response hasn't already been settled locally before allowing the timeout claim.

### Recommendation
In `dispatchIncoming(GetResponse memory response, address relayer)`, delete `_requestCommitments[commitment]` immediately after the fee transfer succeeds (mirroring the deletion already done in `dispatchTimeOut`), so the commitment cannot be paid out a second time through the timeout branch. Additionally, `handleGetRequestTimeouts` should reject timeout proofs for any commitment whose fee metadata has already been cleared (i.e., re-check `meta.sender != address(0)` is sufficient once the response path also clears it), and ideally require timeout proofs to be evaluated only against a height that is not older than any height at which the corresponding response has already been recorded.

### Proof of Concept
1. Attacker (as relayer, or colluding with the relayer) submits a valid `GetResponseMessage` via `HandlerV2.handleGetResponses` for a GET request they issued; `dispatchIncoming(GetResponse,...)` succeeds, the fee is transferred to the relayer, `GetRequestHandled` is emitted — but `_requestCommitments[commitment]` is left populated. [4](#0-3) 
2. The attacker later obtains (or already holds) a state-machine commitment at an earlier height stored in `_stateCommitments`, from before the response was locally recorded, along with a state-trie non-membership proof for the response receipt at that height.
3. Attacker calls `HandlerV2.handleGetRequestTimeouts` with that older height; `meta.sender == address(0)` check passes because `_requestCommitments[commitment]` was never cleared, and the non-membership proof against the old root validates. [5](#0-4) 
4. `EvmHost.dispatchTimeOut` pays `meta.fee` again via `safeTransfer`, resulting in the same commitment's fee being paid out twice. [6](#0-5)

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
