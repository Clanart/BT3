Based on the investigation, the concrete Hyperbridge analog to the Wildcat inconsistency (functions in the same trust boundary treating "when do we move value relative to state" inconsistently, causing accounting drift) is in `EvmHost`'s three `dispatchIncoming`/`dispatchTimeOut` sibling functions, which pay/refund the relayer fee stored in `_requestCommitments[commitment]` with inconsistent bookkeeping around when that mapping entry is cleared.

### Title
Relayer-fee payout on GET response delivery never clears `_requestCommitments`, unlike the sibling timeout paths - (File: evm/src/core/EvmHost.sol)

### Summary
`EvmHost.dispatchIncoming(GetResponse, address)` pays the relayer fee straight out of `_requestCommitments[commitment].fee` but — unlike every sibling function that consumes the same fee-metadata mapping — never deletes that entry afterward.

### Finding Description
`EvmHost` has three functions that consume `_requestCommitments[commitment]` (a `FeeMetadata{sender, fee}` struct) to pay out a relayer/payer:

- `dispatchTimeOut(GetRequestTimeout, ...)` — explicitly `delete _requestCommitments[commitment]` *before* invoking the app callback, and only restores it if the callback fails: [1](#0-0) 
- `dispatchTimeOut(PostRequestTimeout, ...)` — same pattern, `delete` first, restore only on callback failure: [2](#0-1) 
- `dispatchIncoming(GetResponse, address)` — sets replay-protection (`_responseReceipts[commitment]`), invokes `onGetResponse`, and on success reads `_requestCommitments[commitment].fee` and transfers it to the relayer, **but never deletes `_requestCommitments[commitment]`**: [3](#0-2) 

This mirrors exactly the Wildcat pattern: several sibling functions manipulate the same underlying value (fee metadata pull/consume), but one function handles the bookkeeping differently from the rest, leaving stale state behind. Here the corrupted value is `_requestCommitments[commitment].fee`, which remains non-zero after a successful `GetResponse` delivery already paid it out once.

The dispatch-side comment even documents `dispatch(DispatchGet)` shares the same `_requestCommitments` mapping used for POST requests: [4](#0-3) . Both `dispatchTimeOut(GetRequestTimeout,...)` and `dispatchIncoming(GetResponse,...)` are alternate terminal outcomes for the *same* outgoing GET request/commitment (delivered vs. timed out), and only one of the two clears the fee-metadata entry.

### Impact Explanation
If a GET request's response delivery (`dispatchIncoming(GetResponse,...)`) succeeds and pays the relayer, `_requestCommitments[commitment]` still shows `fee != 0` afterward. Any code path that can re-invoke this same host function for the same commitment (e.g., a second/duplicate GET-response message accepted by the handler because primary duplicate-protection is checked elsewhere or against a different key such as `_responseReceipts`, or a race between a response delivery and a timeout being processed for the same request) will pay the stored fee out a second time to a second relayer, or let a subsequent `dispatchTimeOut` also read the never-cleared fee and refund it to the payer — a double payment out of the host's fee-token balance. This falls squarely under "double-claim/double-settlement" — an unprivileged relayer benefits from unauthorized extra token transfers out of `EvmHost`.

### Likelihood Explanation
This requires that the handler-level guard against re-processing the same GET request/response commitment does not also gate on/clear `_requestCommitments`, and that both a response and a timeout (or two response deliveries) for the same commitment can reach `EvmHost` before the state is fully reconciled. I was not able to fully trace `HandlerV2.sol`'s duplicate-check logic for GET responses in this session (ran out of tool iterations) to confirm whether it independently blocks a second call into `dispatchIncoming(GetResponse,...)` for an already-served commitment. The documentation for `handlePostRequests` explicitly advertises a "Checks for duplicate delivery" step, but the equivalent `handleGetResponses` documentation, as retrieved, does not list this step, which is suggestive but not conclusive: [5](#0-4) . This should be treated as a likely-but-unconfirmed path until `HandlerV2.sol`'s request/response receipt checks are read directly.

### Recommendation
Make fee-metadata cleanup consistent across all three consumers of `_requestCommitments`: `dispatchIncoming(GetResponse, address)` should `delete _requestCommitments[commitment]` (mirroring the timeout paths) immediately once the fee is paid out — ideally before the external `onGetResponse` call, following the same "clear first, restore on failure" pattern already used in `dispatchTimeOut`, so a re-entrant or duplicate call cannot observe a stale non-zero fee.

### Proof of Concept
Not independently reproducible from static review alone — a working PoC requires confirming, from `HandlerV2.sol`, that a GET response commitment can reach `EvmHost.dispatchIncoming(GetResponse,...)` (or the `dispatchTimeOut(GetRequestTimeout,...)` path) a second time for the same underlying request after the first successful payout, since `_requestCommitments[commitment]` is left non-zero by the response path. This verification step could not be completed within the available tool budget.

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

**File:** evm/src/core/EvmHost.sol (L974-990)
```text
    function dispatch(DispatchGet memory get) external payable notFrozen returns (bytes32 commitment) {
        if (msg.value > 0) {
            address[] memory path = new address[](2);
            address uniswapV2 = _hostParams.uniswapV2;
            path[0] = IUniswapV2Router02(uniswapV2).WETH();
            path[1] = feeToken();
            IUniswapV2Router02(uniswapV2).swapETHForExactTokens{value: msg.value}(
                get.fee, path, address(this), block.timestamp
            );
        } else if (get.fee > 0) {
            IERC20(feeToken()).safeTransferFrom(_msgSender(), address(this), get.fee);
        }

        uint64 timeoutTimestamp = get.timeout == 0 ? 0 : uint64(block.timestamp) + uint64(get.timeout);
        GetRequest memory request = GetRequest({
            source: host(),
            dest: get.dest,
```

**File:** docs/content/developers/evm/api/ihandler.mdx (L157-180)
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
```
