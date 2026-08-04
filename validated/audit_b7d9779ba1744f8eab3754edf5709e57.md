## Analysis

The Connext bug's core invariant break is: **the relayer fee payout is contingent on an external call succeeding, and once that external call is skipped, retried, or replayed, the fee accounting for that delivery is no longer bound to a single, provably-unique settlement**. In Hyperbridge, this same broken-invariant class shows up not as "relayer never gets paid" but as its mirror image: **the relayer fee for GET responses can be paid out more than once for the same request**, because the duplicate-delivery guard that exists for POST requests is absent for GET responses.

`HandlerV2.handlePostRequests` explicitly guards against replay before dispatching: [1](#0-0) 

But `IHandlerV2.handleGetResponses` / `handleGetResponses()` is documented with a materially different, weaker set of guarantees — it lists no duplicate-delivery revert at all, only destination/timeout/known-request/proof checks: [2](#0-1) 

And on the host side, `dispatchIncoming(GetResponse)` unconditionally overwrites `_responseReceipts[commitment]` and pays the relayer fee out of `_requestCommitments[commitment].fee` — a value that is never zeroed/deleted after payment: [3](#0-2) 

Compare this to the sibling POST-timeout and GET-timeout paths, which both explicitly `delete _requestCommitments[commitment]` (or equivalent) as part of one-time settlement: [4](#0-3) [5](#0-4) 

`handleGetResponses` is permissionless, callable by anyone with a valid state proof: [6](#0-5) 

Because the underlying state commitment used to build the storage/membership proof for a given GET request remains valid at the proven height indefinitely (it is not consumed), and `_requestCommitments[commitment].fee` is never cleared after the first payout, a resubmission of the same (or a freshly-constructed, equally valid) `GetResponseMessage` against the same already-answered commitment is not rejected by any code path shown in the handler or host, and pays the fee again.

Given that I was unable to obtain the full body of `HandlerV2.handleGetResponses` (only its opening lines were retrieved) I cannot rule out with 100% certainty that there is an internal duplicate check reading `host.responseReceipts(...)` that mirrors the `host.requestReceipts(...)` check used for POST requests. However, the documentation's own enumerated revert list for `handleGetResponses()` (no `DuplicateMessage`) is inconsistent with `handlePostRequests()`'s documented revert list (which does include `DuplicateMessage`), which is strong circumstantial evidence the guard is missing for this path.

### Title
Missing duplicate-delivery guard and un-cleared fee allow repeated relayer-fee payout on GET responses - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` pays the relayer fee from `_requestCommitments[commitment].fee` on every successful call, and never deletes/zeroes that fee after payment. Unlike the POST-request handler, which explicitly checks `host.requestReceipts(...) != address(0)` before dispatch and reverts with `DuplicateMessage`, the documented behavior of `handleGetResponses()` lists no equivalent duplicate check.

### Finding Description
`dispatchIncoming(GetResponse)` writes `_responseReceipts[commitment]` unconditionally, invokes `onGetResponse`, and — on success — transfers `_requestCommitments[commitment].fee` to `relayer` without deleting or zeroing that fee entry: [3](#0-2) 

For POST requests and both timeout paths, one-time settlement is enforced structurally (receipt/commitment mapping is checked or deleted as part of the same transaction), so a second delivery attempt either reverts (`DuplicateMessage`) or has nothing left to pay out: [1](#0-0) [4](#0-3) 

No such invariant is enforced for `GetResponse`: the fee field the payout reads from is untouched by the payout itself, and the documented `handleGetResponses()` process/revert list omits any duplicate-delivery rejection: [2](#0-1) 

### Impact Explanation
If the duplicate check is indeed absent (as the documentation differential suggests), any address can resubmit a previously-accepted `GetResponseMessage` against the still-valid state commitment and drain the `feeToken` balance held by the host for that fee repeatedly — a direct loss/duplicate-claim of escrowed relayer fee funds, violating the "bridged assets/relayer rewards must move exactly once" invariant central to this bounty's scope.

### Likelihood Explanation
The entrypoint is explicitly permissionless ("can be called by anyone"), requires no privileged relayer/prover/admin role, and the state proof used the first time remains re-provable against the same stored commitment since nothing forces height progression or receipt-based rejection at the handler layer for this message type.

### Recommendation
Add the same duplicate-delivery guard used for POST requests to `handleGetResponses` (revert if `host.responseReceipts(commitment)` already set), and additionally zero out `_requestCommitments[commitment].fee` inside `dispatchIncoming(GetResponse)` immediately after a successful fee transfer, mirroring the deletion pattern already used in the POST/GET timeout paths.

### Proof of Concept
1. Relayer submits a valid `GetResponseMessage` to `HandlerV2.handleGetResponses`; `EvmHost.dispatchIncoming(GetResponse)` pays `fee` from `_requestCommitments[commitment].fee` to `relayer` and never clears it.
2. The same relayer (or any other address) resubmits an equally valid `GetResponseMessage` for the identical `commitment`/proof (the state commitment at that height is still stored and provable).
3. Absent a `responseReceipts`-based duplicate check in `handleGetResponses`, the call proceeds, `dispatchIncoming(GetResponse)` runs again, and `_requestCommitments[commitment].fee` — still non-zero — is transferred again.
4. Repeat until the host's `feeToken` balance for that fee pool is exhausted, at the expense of legitimate fee payers/other relayers.

**Caveat on verification**: I was only able to retrieve the opening portion of `HandlerV2.handleGetResponses` (through `evm/src/core/HandlerV2.sol` line ~220) and the documentation's process list, not the complete function body. If the source code (not reflected in the doc's revert list) does contain a `host.responseReceipts(...)` duplicate check equivalent to the POST-request path, this finding would not hold. This should be confirmed by reading the full `handleGetResponses` implementation in `evm/src/core/HandlerV2.sol` before treating this as conclusively exploitable.

### Citations

**File:** evm/src/core/HandlerV2.sol (L204-209)
```text
        for (uint256 i = 0; i < requestsLen; ++i) {
            PostRequestLeaf memory leaf = request.requests[i];
            // duplicate request?
            if (host.requestReceipts(leaf.request.hash()) != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.request, _msgSender());
        }
```

**File:** docs/content/developers/evm/api/ihandler.mdx (L157-194)
```text
### handleGetResponses()

Processes and delivers GET responses with state data to source applications.

```solidity lineNumbers
function handleGetResponses(
    IHost host,
    GetResponseMessage calldata message
) external
```

| Parameter | Type | Description |
|-----------|------|-------------|
| `host` | `IHost` | The IHost contract |
| `message` | `GetResponseMessage` | Struct containing proof and responses |

**Access:** Permissionless (can be called by anyone)

**Process:**
1. Verifies state proof against stored commitment
2. For each response:
   - Validates destination matches this chain
   - Checks response hasn't timed out
   - Verifies response is for a known request
   - Validates storage proofs for each key
   - RLP-decodes storage values
   - Dispatches to source application

**Important:**
- Storage values are RLP-encoded from the state trie
- Values are provided in same order as requested keys
- Empty storage slots return empty bytes

**Reverts:**
- `InvalidMessageDestination()` - Response not for this chain
- `MessageTimedOut()` - Response exceeded timeout
- `UnknownMessage()` - Request not found
- `InvalidProof()` - State proof verification failed
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
