### Title
`dispatchIncoming(GetResponse)` omits the replay-protection cleanup that its sibling `dispatchTimeOut` performs, allowing a Get request's fee to be paid out twice - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.sol` has two terminal handlers for the same outgoing `GetRequest` lifecycle: `dispatchIncoming(GetResponse ...)` (called when a response arrives) and `dispatchTimeOut(GetRequestTimeout ...)` (called when the request times out). Both read `_requestCommitments[commitment]` to get the fee metadata. `dispatchTimeOut` explicitly deletes `_requestCommitments[commitment]` "// replay protection" before paying out, but `dispatchIncoming(GetResponse ...)` never deletes it after paying the relayer. This is the exact "duplicate logic that diverged between two near-identical functions" pattern from the report: one twin function kept the safety cleanup, the other dropped it.

### Finding Description
`dispatchTimeOut` for both `PostRequestTimeout` and `GetRequestTimeout` starts with: [1](#0-0) 
deleting the commitment first, "for replay protection," before calling the module and refunding `meta.fee` to `meta.sender`.

By contrast, `dispatchIncoming(GetResponse memory response, ...)` reads the same `_requestCommitments[commitment]` to pay the relayer, but never deletes it: [2](#0-1) 

So after a successful `GetResponse` delivery, `_requestCommitments[commitment]` still holds the original non-zero `fee`/`sender` metadata. The only thing stopping a later `GetRequestTimeout` from being accepted for the *same* commitment is a cross-chain non-membership proof checked in `HandlerV2.handleGetRequestTimeouts`, which proves that Hyperbridge's own child trie has no `RESPONSE_RECEIPTS_STORAGE_PREFIX + commitment` entry at the proven height: [3](#0-2) 

That guard is external to `EvmHost.sol` itself — it depends entirely on Hyperbridge's own bookkeeping (`store_response_receipt` in the state-coprocessor pallet) being written, keyed, and proven consistently, and on the timeout proof height being chosen after that record lands. The EVM host contract has **no local invariant** (e.g., checking `_responseReceipts[commitment].relayer == address(0)` or deleting `_requestCommitments`) that prevents `dispatchTimeOut(GetRequestTimeout)` from running against a commitment whose response was already paid.

Note also that the NatSpec comment on `dispatchTimeOut(GetRequestTimeout...)` says "`@notice Does not refund any protocol fees.`" yet the function body unconditionally refunds `meta.fee` to `meta.sender` when non-zero — the code and its own documentation disagree, which is itself evidence the "no double-refund" invariant was not carefully re-verified when this function was written/copied from its POST counterpart. [4](#0-3) 

### Impact Explanation
If a `GetRequestTimeout` is ever accepted for a commitment whose `GetResponse` was already dispatched (i.e., the cross-chain non-membership proof's implicit assumptions fail to hold for any reason — stale/ misordered proof height relative to when Hyperbridge records the response receipt, a bug in the receipt key derivation, or any timing gap between response processing and receipt persistence), the host pays the collected fee out **twice**: once to the relayer via `dispatchIncoming(GetResponse ...)`, and again to `meta.sender` via `dispatchTimeOut(GetRequestTimeout ...)`, because the metadata was never cleared by the first payout. This is a direct loss of protocol/`feeToken` funds paid from the host's own balance — matching the bounty's "stealing or loss of funds" / "double-claim / double-settlement" categories.

### Likelihood Explanation
This is not a purely theoretical divergence: the code shows the deletion is present in one sibling function (`dispatchTimeOut`) and structurally absent in the other (`dispatchIncoming(GetResponse)`) that shares the exact same `_requestCommitments` state and the same "must not pay twice" invariant. Whether the cross-chain non-membership proof in `handleGetRequestTimeouts` reliably closes this gap in all cases could not be fully verified from the EVM-side code alone — it depends on Substrate-side ordering guarantees in `modules/pallets/state-coprocessor/src/impls.rs` that are outside `EvmHost.sol`. Because the local contract carries no defense-in-depth check of its own, any weakness or edge case in that separate cross-chain proof path becomes directly exploitable for double payment.

### Recommendation
- In `dispatchIncoming(GetResponse memory response, ...)`, delete `_requestCommitments[commitment]` (mirroring `dispatchTimeOut`'s "replay protection" pattern) once the fee has been paid to the relayer, so a subsequent `dispatchTimeOut(GetRequestTimeout ...)` call for the same commitment fails the `meta.sender == address(0)` check in `HandlerV2.handleGetRequestTimeouts` (`UnknownMessage`), instead of relying solely on an external cross-chain non-membership proof.
- Fix or remove the misleading `@notice Does not refund any protocol fees.` comment on `dispatchTimeOut(GetRequestTimeout ...)` to match its actual behavior.
- Add a regression test asserting that a `GetRequestTimeout` cannot be processed for a commitment that has already received a successful `GetResponse` on the same EVM host, independent of the Hyperbridge-side proof.

### Proof of Concept
1. Dispatch a `GetRequest` from `EvmHostA` with a non-zero fee; `_requestCommitments[commitment] = {sender, fee}` is stored.
2. `handleGetResponses` on `EvmHostA` delivers a valid `GetResponse` for `commitment`; `EvmHost.dispatchIncoming(GetResponse, relayer)` runs, pays `fee` to `relayer`, and stores `_responseReceipts[commitment]` — but `_requestCommitments[commitment]` is left untouched (still `{sender, fee}`). [5](#0-4) 
3. If a relayer later submits a `GetTimeoutMessage` for the same `commitment` with a state proof at a height/point where Hyperbridge's `RESPONSE_RECEIPTS_STORAGE_PREFIX+commitment` non-membership check can be satisfied (e.g., due to any inconsistency in when/how that receipt is written on the Substrate side), `HandlerV2.handleGetRequestTimeouts` passes the `meta.sender == address(0)` check (it is not zero) and calls `EvmHost.dispatchTimeOut(GetRequestTimeout, meta, commitment)`. [3](#0-2) 
4. `dispatchTimeOut` refunds `meta.fee` a second time to `meta.sender`, so the same fee has now been paid out twice from the host's `feeToken` balance for a single request. [6](#0-5)

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

**File:** evm/src/core/HandlerV2.sol (L293-320)
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
```
