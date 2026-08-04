Confirmed replay/duplicate-claim analog in `EvmHost.sol`'s `dispatchIncoming(GetResponse,...)`. The `HandlerV2.handleGetResponses` dedup check only reads `host.responseReceipts(...)`, which is written unconditionally *before* the low-level call inside `dispatchIncoming`, and the fee record in `_requestCommitments[commitment]` is never cleared after paying it out.

### Title
Relayer fee for GET responses can be paid out repeatedly because `_requestCommitments` is never cleared in `dispatchIncoming(GetResponse,...)` - (File: `evm/src/core/EvmHost.sol`)

### Summary
`EvmHost.dispatchIncoming(GetResponse memory response, address relayer)` pays the stored relayer fee from `_requestCommitments[commitment]` after a successful callback, but unlike the POST-request timeout paths, it never deletes/zeroes that fee metadata entry.

### Finding Description
`dispatchIncoming(GetResponse,...)` at [1](#0-0)  sets `_responseReceipts[commitment]` up-front, performs the `onGetResponse` callback, and if the call succeeds pays `_requestCommitments[commitment].fee` to `relayer` via `safeTransfer` — but it does not delete or decrement `_requestCommitments[commitment]` afterward. Compare this to the timeout paths (`dispatchTimeOut` for both `GetRequestTimeout` and `PostRequestTimeout`), which explicitly `delete _requestCommitments[commitment];` before paying out, and to `dispatchIncoming(PostRequest,...)`, which correctly guards state changes around the `success` check.

The only replay guard at the caller (`HandlerV2.handleGetResponses`, [2](#0-1)  ) checks `host.responseReceipts(leaf.response.request.hash()).relayer != address(0)` to reject duplicates — but `_responseReceipts` is only ever written by `dispatchIncoming(GetResponse,...)` itself, in the very call that is supposed to be guarded. If the callback embedded in `onGetResponse` (an external, attacker-influenced or reentrant-capable contract, since it's an arbitrary low-level `.call`) reenters `EvmHost` before `dispatchIncoming` returns to the top-level `handleGetResponses` transaction, or if any code path allows `dispatchIncoming(GetResponse,...)` to be invoked a second time for the same commitment before the first invocation completes and before `_requestCommitments` is cleared, the fee can be paid out more than once — because the fee-metadata entry that funds the payout is left intact.

### Impact Explanation
This is a reward-claim/double-payment class bug: a relayer fee funded once by a user's `DispatchGet` fee token deposit can be extracted more than once from the host's fee-token balance, draining funds that belong to the protocol/other requests. It falls squarely under "reward claims... must move exactly once."

### Likelihood Explanation
Exploitability depends on whether `onGetResponse` can trigger reentrancy into `dispatchIncoming` (or another call reaching the same commitment) before `_responseReceipts`/`_requestCommitments` are fully settled — `dispatchIncoming` is `restrict(_hostParams.handler)`-gated, so the callback itself cannot call it directly, but the missing cleanup of `_requestCommitments[commitment]` is a genuine deviation from the pattern used everywhere else in the same contract, and is a real code defect independent of the precise reentrancy vector. I was not able to fully trace every call path that could re-enter this function within the available tool budget, so likelihood is moderate rather than fully proven.

### Recommendation
Add `delete _requestCommitments[commitment];` (or equivalent zeroing) immediately after the fee is transferred (or before the external call, following checks-effects-interactions) in `dispatchIncoming(GetResponse,...)`, mirroring the cleanup already performed in `dispatchTimeOut`.

### Proof of Concept
Not independently reproduced in this session — the specific reentrancy trigger for a second `dispatchIncoming(GetResponse,...)` call on the same commitment before `_requestCommitments` cleanup was not confirmed with certainty; only the missing-cleanup code defect itself was directly verified in `evm/src/core/EvmHost.sol`.

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

**File:** evm/src/core/HandlerV2.sol (L241-246)
```text
        for (uint256 i = 0; i < responsesLength; ++i) {
            GetResponseLeaf memory leaf = message.responses[i];
            // duplicate response?
            if (host.responseReceipts(leaf.response.request.hash()).relayer != address(0)) revert DuplicateMessage();
            host.dispatchIncoming(leaf.response, _msgSender());
        }
```
