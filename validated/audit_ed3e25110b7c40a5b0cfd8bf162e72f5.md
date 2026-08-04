## Analysis

The FactoryDAO bug's core invariant break is: **a single unpayable/blacklisted beneficiary transfer, embedded inside a loop with no per-item isolation, permanently blocks a batched operation for every other participant sharing that batch.**

The direct Hyperbridge analog is in the POST-timeout refund path: `HandlerV2.handlePostRequestTimeouts` batches many unrelated timed-out requests (sharing one state-commitment proof) into a single call, and `EvmHost.dispatchTimeOut(PostRequestTimeout)` refunds the relayer fee with an un-isolated `safeTransfer` after the app callback succeeds.

### Title
Unisolated fee-refund transfer in batched POST-timeout processing lets one unpayable `payer` permanently block refunds for every other request in the batch - (File: `evm/src/core/HandlerV2.sol`, `evm/src/core/EvmHost.sol`)

### Summary
`handlePostRequestTimeouts` iterates a caller-supplied array of timed-out requests sharing a single non-membership proof, invoking `host.dispatchTimeOut(...)` for each with no try/catch isolation between items. Inside `EvmHost.dispatchTimeOut(PostRequestTimeout,...)`, the app callback failure *is* isolated (via low-level `.call()` + boolean check, with commitment restoration for retry), but the subsequent relayer-fee refund `IERC20(feeToken()).safeTransfer(meta.sender, meta.fee)` is a direct call with no isolation. If it reverts, the revert propagates all the way up through the `for` loop in `handlePostRequestTimeouts`, reverting the entire batch transaction — including the timeout processing of every unrelated request bundled alongside it.

### Finding Description [1](#0-0) 

```solidity
for (uint256 i = 0; i < timeoutsLength; ++i) {
    PostRequest memory request = message.timeouts[i];
    ...
    host.dispatchTimeOut(PostRequestTimeout(request, _msgSender()), meta, requestCommitment);
}
``` [2](#0-1) 

```solidity
function dispatchTimeOut(
    PostRequestTimeout memory timeout,
    FeeMetadata memory meta,
    bytes32 commitment
) external restrict(_hostParams.handler) {
    delete _requestCommitments[commitment];
    (bool success,) = _bytesToAddress(timeout.request.from)
        .call(abi.encodeWithSelector(IApp.onPostRequestTimeout.selector, timeout));

    if (!success) {
        _requestCommitments[commitment] = meta;
        return;
    }

    if (meta.fee != 0) {
        // refund relayer fee
        IERC20(feeToken()).safeTransfer(meta.sender, meta.fee);
    }
    emit PostRequestTimeoutHandled(...);
}
```

`meta.sender` is `post.payer`, an address the request's originator fully controls at dispatch time (`_requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee})` in `dispatch()`, [3](#0-2) ). If `feeToken()` is a blacklist-capable stablecoin (e.g. USDC/USDT-style, common as a bridging fee token) and `payer` is (or becomes) blacklisted/paused, `safeTransfer` reverts unconditionally and forever for that specific request — with no isolation, this bubbles out of `dispatchTimeOut` and reverts the whole `handlePostRequestTimeouts` transaction.

This mirrors the analogous GET-timeout path (`dispatchTimeOut(GetRequestTimeout,...)`) and the same unguarded pattern in [4](#0-3) .

Contrast this with the on-timeout callback handling one line above it, and with the Substrate side (`modules/ismp/core/src/handlers/timeout.rs`), where a failing callback restores the commitment for isolated retry — the refund step lacks the equivalent guard.

### Impact Explanation
A relayer that batches multiple pending timeouts sharing one state-commitment proof into a single `handlePostRequestTimeouts` call (the natural, gas-efficient behavior, not malicious relayer behavior) will have the *entire batch* revert if any single bundled request's `payer` cannot receive the fee-token refund. This locks relayer-fee refunds for every other unrelated payer whose timeout happened to land in the same batch, and — because the failure is deterministic and permanent (a permanently blacklisted address never becomes payable) — the malicious request can be resubmitted indefinitely bundled with fresh unrelated timeouts, repeatedly griefing refunds. This is a fund-lock/DoS on a legitimate settlement path reachable by an unprivileged, ordinary user.

### Likelihood Explanation
Likelihood depends on the deployed `feeToken()` supporting a blacklist/pause mechanism (true for several EVM hosts using USDC/USDT-class tokens) and on relayers naturally batching multiple pending timeouts sharing one proof height, which is standard behavior for amortizing proof-verification gas costs. No compromised relayer, prover, or admin is required — only a user who dispatches a request with `payer` set to an address vulnerable to the token's blacklist mechanism.

### Recommendation
Wrap the relayer-fee refund transfer in `dispatchTimeOut` (both `PostRequestTimeout` and `GetRequestTimeout` variants) in a low-level `call` with success checking, mirroring the pattern already used for the app callback: on transfer failure, restore `_requestCommitments[commitment]` so the item is retried independently, and do not let the failure propagate to abort sibling entries in the batch, or handle the failure per-item inside `handlePostRequestTimeouts`'s loop rather than allowing an unguarded revert to escape one call site into a shared loop.

### Proof of Concept
1. Attacker dispatches a `PostRequest` via `EvmHost.dispatch(DispatchPost{...})` on the source chain, setting `post.payer` to an address they control and can later have blacklisted on `feeToken()` (or that is already on the fee token's blacklist), with `post.fee > 0`.
2. The request never gets delivered (or the destination rejects/times out), so it expires per `leaf.request.timeout()`.
3. A relayer, following normal batching behavior, submits `handlePostRequestTimeouts` including this timed-out request together with N other unrelated timed-out requests sharing the same non-membership proof height.
4. Loop reaches attacker's entry: `dispatchTimeOut` succeeds through the `onPostRequestTimeout` callback, then reverts on `IERC20(feeToken()).safeTransfer(meta.sender, meta.fee)` because `meta.sender` is blacklisted.
5. The revert propagates out of `dispatchTimeOut` and through the `for` loop in `handlePostRequestTimeouts`, reverting the whole transaction — none of the N other legitimate payers get their relayer-fee refunds processed in that transaction, and the same griefing recurs on every retry attempt that bundles the attacker's request with others.

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

**File:** evm/src/core/EvmHost.sol (L841-847)
```text
        // reward the relayer fee
        uint256 fee = _requestCommitments[commitment].fee;
        if (fee != 0) {
            IERC20(feeToken()).safeTransfer(relayer, fee);
        }
        emit GetRequestHandled({commitment: commitment, relayer: relayer});
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

**File:** evm/src/core/EvmHost.sol (L946-948)
```text
        // make the commitment
        commitment = request.hash();
        _requestCommitments[commitment] = FeeMetadata({sender: post.payer, fee: post.fee});
```
