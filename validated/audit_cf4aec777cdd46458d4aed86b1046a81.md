## Finding [1](#0-0) 

### Title
Relayer fee for GET responses is never cleared from `_requestCommitments`, enabling duplicate fee payout on resubmission - (File: evm/src/core/EvmHost.sol)

### Summary
The M-10 bug pattern is: a fixed fee (settlement fee) keeps getting charged/paid out against a stored record that is never marked "consumed" once its associated unit of work is done. In `EvmHost.sol`, the timeout paths for both GET and POST requests explicitly `delete _requestCommitments[commitment]` before refunding the fee to the payer, but the success path for `dispatchIncoming(GetResponse)` reads `_requestCommitments[commitment].fee`, pays it to `relayer`, and never deletes the entry.

### Finding Description
Compare the three sibling functions in `EvmHost.sol`:

- `dispatchTimeOut(GetRequestTimeout...)`: `delete _requestCommitments[commitment];` then calls the app callback, refunds fee only if the callback fails is re-storable, i.e. commitment metadata is deleted up front. [2](#0-1) 

- `dispatchTimeOut(PostRequestTimeout...)`: same pattern — `delete _requestCommitments[commitment];` first. [3](#0-2) 

- `dispatchIncoming(GetResponse...)`: sets `_responseReceipts[commitment]` (unconditionally, with no "already delivered" check before the write) and, on a successful `onGetResponse` callback, reads `_requestCommitments[commitment].fee` and transfers it to `relayer` — **but never deletes `_requestCommitments[commitment]`**. [1](#0-0) 

This is the same class of bug as M-10: a value meant to represent "the fee owed for this unit of work" is treated as permanent/always-active bookkeeping instead of being retired once it has served its purpose, so it keeps getting paid out. In the vault report the fixed fee kept being *charged* against a decommissioned market; here the fixed fee keeps being *available to pay out* against a request whose fee has already been claimed once, because the record that should gate a one-time payment is never cleared.

The `_responseReceipts[commitment]` write is a blind overwrite, not a compare-and-set guard — the function does not check whether `_responseReceipts[commitment].relayer` is already non-zero before proceeding, unlike the documented duplicate-check pattern used for POST-request delivery via `requestReceipts(commitment) != address(0)`. That means the "replay protection" comment does not correspond to an actual revert-on-duplicate check inside `EvmHost` itself; whatever protects against re-invocation lives entirely in the calling `Handler`. If the handler's own duplicate check is bypassed, weakened, or the same GET request is ever resubmitted for delivery (e.g., after a transient failure that partially completed, or via a codepath that reaches `dispatchIncoming(GetResponse)` more than once for the same commitment), the `_requestCommitments[commitment].fee` value is still sitting there and will be paid out again to whichever `relayer` triggers the second delivery.

### Impact Explanation
If reachable, this is a direct double-payment of the relayer fee: the fee token escrowed by the payer at `dispatch()` time is transferred out on every successful `dispatchIncoming(GetResponse)` invocation for the same commitment, instead of exactly once. This falls under "double-claim/double-settlement" and "stealing or loss of funds" in the bounty's impact list, since fee tokens held by `EvmHost` (from `RequestFunded`/`dispatch`) could be drained beyond the amount actually owed.

### Likelihood Explanation
This is not exploitable purely from `EvmHost.sol` in isolation — the missing `delete` only becomes a live bug if the `Handler` contract's duplicate-delivery guard for GET responses does not itself provide an unconditional one-time-only invariant equivalent to `requestReceipts` for POST requests. I was not able to fully confirm from the indexed content whether `HandlerV2.handleGetResponses` enforces an unconditional revert-on-duplicate check (the only duplicate-check snippet retrieved was for `handlePostRequests`, checking `host.requestReceipts(commitment)`). This is a real, code-level asymmetry inside `EvmHost.sol` regardless, but the exact exploitability depends on Handler-side protections I could not fully verify from the available index (the indexer may have size limits on `HandlerV2.sol`'s full body).

### Recommendation
Delete `_requestCommitments[commitment]` in `dispatchIncoming(GetResponse)` immediately after (or before) transferring the fee, mirroring the pattern already used in both `dispatchTimeOut` overloads. Additionally, guard the `_responseReceipts[commitment]` write with an explicit check that reverts if a receipt already exists, rather than relying solely on the calling `Handler` to prevent resubmission.

### Proof of Concept
1. A GET request is dispatched with `post.fee > 0`; `_requestCommitments[commitment] = FeeMetadata{sender, fee}` is set.
2. `dispatchIncoming(GetResponse)` is called once, the app callback succeeds, and `fee` is paid to `relayer1`. `_requestCommitments[commitment]` is **not** cleared.
3. If the same response can be redelivered to `dispatchIncoming(GetResponse)` a second time (bypassing or absent an unconditional Handler-side duplicate check equivalent to `requestReceipts`), the function reads the same non-zero `fee` from `_requestCommitments[commitment]` and pays it again to `relayer2`, doubling the payout for a single delivered response.

Because I could not fully verify the Handler-side duplicate-delivery guard within the indexed portion of `HandlerV2.sol`, this should be treated as a confirmed code-level defect (missing state cleanup) whose full exploitability depends on code I could not completely inspect — starting a full Devin session against the repository would allow reading the complete `HandlerV2.sol` to confirm or rule out the duplicate-submission path.

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
