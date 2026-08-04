## Analog Found: Un-cleared relayer-fee ledger enables duplicate GET-response reward payout — (File: `evm/src/core/EvmHost.sol`)

### Summary
The Morpho report's core defect is a **ledger that is not invalidated after the underlying value has already moved**: Aave changes the position (partial repay + collateral seizure) but Morpho's internal accounting keeps stale amounts, so the same debt can be "settled" again. `EvmHost.sol`'s GET-response delivery path has the same shape: the relayer-fee ledger entry `_requestCommitments[commitment]` is read and paid out on a successful delivery, but — unlike every other terminal path in the same contract — it is never deleted afterward, leaving the fee "still owed" in storage after it has already been transferred.

### Finding Description
`EvmHost.dispatchIncoming(GetResponse, address relayer)` pays the relayer fee straight out of the fee-metadata mapping without clearing it: [1](#0-0) 

Compare this to the sibling success/failure paths in the exact same contract, all of which treat clearing `_requestCommitments`/`_requestReceipts` as mandatory "replay protection":

- `dispatchIncoming(PostRequest, ...)` sets `_requestReceipts[commitment] = relayer` as its replay guard.
- `dispatchTimeOut(GetRequestTimeout, ...)` explicitly does `delete _requestCommitments[commitment];` *before* invoking the callback, re-storing it only if the callback fails, precisely to prevent the fee metadata from being reused.
- `dispatchTimeOut(PostRequestTimeout, ...)` follows the identical delete-then-restore-on-failure pattern. [2](#0-1) 

In the GET-response success path, however, `_requestCommitments[commitment].fee` is read and paid to `relayer` via `safeTransfer`, but the entry is left standing in storage. The only write that happens on success is `_responseReceipts[commitment] = ResponseReceipt(...)`, and this assignment is **unconditional** — it does not check whether a `ResponseReceipt` already exists for that commitment, so it is not itself a guard against re-entry into this function; it merely overwrites whatever was there. Anything that gates repeat calls into `dispatchIncoming(GetResponse,...)` must therefore live entirely in the caller, `HandlerV2.handleGetResponses()`, which — per the documented processing steps ("verifies state proof… validates destination… checks response hasn't timed out… verifies response is for a known request") — is not documented to check `_responseReceipts` for an already-delivered commitment before calling into the host, unlike the explicit `DuplicateMessage()` check that the documented POST-request delivery path performs against `_requestReceipts`.

The corrupted value is `_requestCommitments[commitment].fee`: it should become `0`/deleted the instant the relayer is paid, exactly like it does in both timeout paths, but on the success path it survives untouched, meaning **the mapping still asserts a fee is payable for a commitment whose fee has already been transferred out of the contract** — this is the direct analog of Morpho's stale 1 ETH/2500 DAI position surviving a liquidation that already happened underneath it.

### Impact Explanation
If `handleGetResponses` can be invoked a second time for the same GET request/response commitment (e.g., a second relayer submits a still-valid MMR/state membership proof for the same finalized response, or the same relayer resubmits before any receipt-based short-circuit is checked), `dispatchIncoming(GetResponse,...)` will execute its full success branch again: the destination module's `onGetResponse` fires a second time, and — because `_requestCommitments[commitment].fee` was never cleared — the host contract pays the relayer fee out of its fee-token balance a second time for the same delivered response. This is an unauthorized/duplicate transfer of protocol funds (fee token drained from `EvmHost`) with no bound on repetition other than how many times a valid proof for the same finalized state can be resubmitted, directly matching the bounty's "replay/double-claim/double-settlement" and "stealing or loss of funds" categories.

### Likelihood Explanation
This is a public, permissionless entrypoint: relayer fee delivery is explicitly "permissionless (can be called by anyone)" per the handler documentation, and the vulnerable code path requires no privileged role, no malicious peer, and no compromised operator — only a second valid proof submission for an already-finalized response, which any honest actor is capable of assembling since the underlying state root and membership facts remain provable indefinitely. The asymmetry between this function and its sibling timeout handlers (which meticulously delete-then-restore the exact same mapping) indicates the missing `delete` here is an omission rather than an intentional design choice, raising confidence that the guard against re-entry is either absent or resides only in an off-path duplicate check that is not evidenced in the available handler documentation.

### Recommendation
Delete `_requestCommitments[commitment]` before (or immediately after, with restore-on-failure) transferring the fee in `dispatchIncoming(GetResponse, address relayer)`, mirroring the pattern already used in `dispatchTimeOut(GetRequestTimeout,...)` and `dispatchTimeOut(PostRequestTimeout,...)`. Additionally, `HandlerV2.handleGetResponses` should explicitly reject any response whose commitment already has a non-empty `_responseReceipts` entry, the same way POST-request delivery rejects `DuplicateMessage()` via `requestReceipts`.

### Proof of Concept
1. A source-chain app dispatches a `DispatchGet` with a non-zero `fee`, creating `_requestCommitments[commitment] = FeeMetadata({sender: payer, fee: F})`.
2. Relayer A finalizes the GET response through Hyperbridge and calls `HandlerV2.handleGetResponses`, which calls `EvmHost.dispatchIncoming(GetResponse, relayerA)`. The destination module's `onGetResponse` succeeds; `EvmHost` pays `F` fee tokens to `relayerA`; `_requestCommitments[commitment]` is left unchanged (fee = `F` still recorded).
3. Because the underlying state root/membership proof for the same finalized response remains valid indefinitely, relayer B (or relayer A again) resubmits the identical `GetResponseMessage` calldata to `handleGetResponses`.
4. If `handleGetResponses`/`dispatchIncoming` does not reject based on an existing `_responseReceipts[commitment]`, the module callback fires again and `EvmHost` pays out `F` fee tokens a second time from `_requestCommitments[commitment].fee`, which was never cleared — draining the host's fee-token balance for a delivery that was already paid for, exactly mirroring the Morpho pattern of a stale internal balance being acted on twice after the real underlying event already occurred.

**Uncertainty / what needs verification**: I was not able to read the full body of `HandlerV2.handleGetResponses()` in this session (only its documented behavior) to confirm definitively whether it independently checks `_responseReceipts[commitment] != 0`/some other single-delivery guard before invoking `dispatchIncoming(GetResponse,...)`. The vulnerability as identified rests on the confirmed fact that `EvmHost.sol` itself never clears `_requestCommitments[commitment]` on the GET-response success path (unlike its timeout counterparts), which is a real, provable code asymmetry; whether it is actually exploitable in production depends on whether `HandlerV2` independently closes that gap. I recommend a Devin session read `evm/src/core/HandlerV2.sol`'s `handleGetResponses` function in full to confirm or rule out an existing duplicate-delivery guard before treating this as conclusively exploitable.

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

**File:** evm/src/core/EvmHost.sol (L856-906)
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
