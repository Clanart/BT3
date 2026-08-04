### Title
Gas-griefing via untrusted timeout callback permanently DoSes GET/POST request timeout batches - ([File: evm/src/core/EvmHost.sol])

### Summary
`EvmHost.dispatchTimeOut` (both the `GetRequestTimeout` and `PostRequestTimeout` overloads) implements the same "non-blocking callback" pattern flagged in the external LayerZero report: it clears a storage slot, makes an untrusted external call forwarding effectively all remaining gas, and on failure tries to **restore** the just-cleared storage (a zero→non-zero SSTORE) before returning gracefully. An attacker who controls the destination callback (`timeout.request.from`, an address they fully control since they authored the original GET/POST request) can burn gas down to just below the SSTORE floor before reverting, which makes the restoring write itself run out of gas. Because `dispatchTimeOut` is invoked from `HandlerV2` with a plain external call inside a `for` loop over a whole batch of timeouts, this out-of-gas failure propagates and reverts the **entire batch transaction**, not just the malicious entry.

### Finding Description
In `EvmHost.sol`: [1](#0-0) 

and the POST analog: [2](#0-1) 

Both functions:
1. `delete _requestCommitments[commitment]` — clears the `FeeMetadata` mapping entry (replay protection) **before** the external call.
2. Call `.call(...)` on `timeout.request.from` with no gas cap, forwarding (per EIP‑150) 63/64 of all remaining gas to the untrusted destination app's `onGetTimeout`/`onPostRequestTimeout`.
3. On `!success`, attempt `_requestCommitments[commitment] = meta;` to restore the entry "so that it can be retried" — this is a cold zero→non-zero SSTORE of a two-field struct (`sender`, `fee`), which costs at least the 20,000-gas-per-slot floor (~40k+ total).

`timeout.request.from` is the module address the *attacker themselves* specified when they originally dispatched the GET/POST request — it is entirely attacker-controlled. The attacker's malicious contract can measure `gasleft()` and burn gas dynamically so that, no matter how much gas the relayer/self-relayer supplies to the outer transaction, execution always returns to `dispatchTimeOut` with less than the SSTORE floor remaining, guaranteeing the restore statement runs out of gas and reverts.

Crucially, `dispatchTimeOut` is invoked from a loop in `HandlerV2`, not via `try/catch` or a bounded low-level call: [3](#0-2) [4](#0-3) 

Since `host.dispatchTimeOut(...)` is a normal external call inside the `for` loop, an OOG revert deep inside it is not caught anywhere — it bubbles all the way up and reverts `handlePostRequestTimeouts`/`handleGetRequestTimeouts` in full. That rolls back the initial `delete _requestCommitments[commitment]` too, so the request commitment is left exactly as it was — meaning this specific timeout can **never** be successfully processed (every resubmission hits the identical gas-griefing outcome), and any *other, legitimate* timeouts bundled in the same batch are also reverted and blocked from being processed in that transaction.

### Impact Explanation
This is a logic attack on the timeout/refund path, not a peer/relayer/prover compromise: the attacker only needs to dispatch an ordinary GET/POST request from a malicious contract they control, then let it time out. Legitimate relayer fee refunds tied to the poisoned commitment become permanently unprocessable (the module callback path can never complete without reverting), and — because timeouts are processed in caller-supplied batches — an attacker can poison unrelated legitimate timeouts by ensuring their malicious request's timeout proof is (or can be) included alongside them, causing repeated reverts and blocking refunds/settlement for other users' funds too. This is fund-lock/DoS on relayer-fee settlement in the production EVM host contract, matching the bounty's "loss of funds" / "logic attacks" category.

### Likelihood Explanation
Medium-to-high: the attacker needs no special privileges, keys, or relayer/prover cooperation — only the ability to dispatch a GET/POST request from their own contract (a normal user action) and later trigger its timeout processing (a permissionless call). The gas-griefing technique (loop until `gasleft()` crosses a threshold, then revert) is standard and reliable, independent of how much gas the outer transaction supplies.

### Recommendation
- Bound the gas forwarded to `IApp.onGetTimeout`/`onPostRequestTimeout`/`onAccept`/`onGetResponse` callbacks explicitly (e.g. `.call{gas: FIXED_LIMIT}(...)`), and reserve enough gas headroom *after* the call for the failure-path bookkeeping (the SSTORE restore + event) regardless of what the callback consumes.
- Consider avoiding the "delete-then-restore" pattern for replay protection; e.g., mark commitments with a status enum that only ever transitions cleanly (or performs the restore before the external call and only clears fully on success), so the failure path never requires a cold zero→non-zero SSTORE gated behind an attacker-influenced amount of leftover gas.
- Use `excessivelySafeCall`-style bounded/truncated calls with an explicit minimum-gas check (verify `gasleft() >= threshold` before making the external call) so failures can be handled without risking OOG on the bookkeeping step.
- Isolate per-item failures in `handlePostRequestTimeouts`/`handleGetRequestTimeouts` (e.g., wrap `host.dispatchTimeOut(...)` in a try/catch or make it a low-level call) so one malicious/griefing entry cannot revert an entire batch of legitimate timeouts.

### Proof of Concept
1. Attacker deploys `EvilApp` implementing `IApp.onGetTimeout` (and/or `onPostRequestTimeout`) that does:
   ```solidity
   function onGetTimeout(GetTimeout memory) external {
       while (gasleft() > 15000) { /* burn gas, e.g. keccak loop */ }
       revert();
   }
   ```
2. Attacker dispatches a GET (or POST) request via `EvmHost.dispatch(...)` with `from = EvilApp` (attacker's own contract) and a short timeout, paying a nonzero fee.
3. Once timed out, anyone (attacker or a relayer) calls `HandlerV2.handleGetRequestTimeouts` (or `handlePostRequestTimeouts`) with a batch containing this timeout, optionally alongside other legitimate timeouts.
4. Inside `dispatchTimeOut`, `_requestCommitments[commitment]` is deleted, then `EvilApp.onGetTimeout` is called, forwarding ~63/64 of remaining gas; `EvilApp` burns gas down to ~15,000, then reverts.
5. `dispatchTimeOut` sees `success == false` and attempts `_requestCommitments[commitment] = meta;`, a cold zero→non-zero SSTORE of a two-field struct requiring >20,000 gas per slot — this runs out of gas and reverts the whole `dispatchTimeOut` call, which in turn reverts `handleGetRequestTimeouts` entirely.
6. Every resubmission of this exact timeout (the proof is fixed/replayable) produces the identical outcome, permanently blocking that refund and any other timeouts bundled in the same transaction.

### Citations

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

**File:** evm/src/core/HandlerV2.sol (L265-286)
```text
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

**File:** evm/src/core/HandlerV2.sol (L301-321)
```text
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
