### Title
Gas-griefing an `HyperbridgeLzEndpoint.onAccept` `lzReceive` callback can revert past the `catch`, rolling back the just-written inbound nonce and permanently bricking the receiver's channel - (`sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol`)

### Summary
`HyperbridgeLzEndpoint.onAccept` advances the inbound nonce for `(receiver, srcEid, sender)` and *then* forwards the message to the receiving OApp via an unguarded `try/catch` call that receives essentially all remaining gas. The developers' own comment states the nonce write is placed *before* the try/catch specifically so a reverting `lzReceive` can never roll back nonce progress and "brick" the channel. That protection only covers an ordinary revert caught by `catch`; it does not cover the case where the receiving OApp consumes gas such that the `catch` block itself (two `SSTORE`s + an event) runs out of gas. In that case the out-of-gas exception is not "caught" — it propagates and reverts the *entire* `onAccept` call, including the earlier nonce write, exactly the outcome the comment says must never happen.

### Finding Description [1](#0-0) 

The nonce is validated and stored at line 380-382, before the external delivery attempt:
```solidity
address receiverAddr = address(uint160(uint256(receiver)));
uint64 expectedNonce = _inboundNonce[receiverAddr][srcEid][sender] + 1;
if (nonce != expectedNonce) revert InvalidNonce(expectedNonce, nonce);
_inboundNonce[receiverAddr][srcEid][sender] = nonce;
```
Immediately after, delivery is attempted with no gas cap, mirroring the exact pattern flagged in the external report (`excessivelySafeCall(gasleft(), ...)` in `NonblockingLzApp._blockingLzReceive`):
```solidity
try ILayerZeroReceiver(receiverAddr).lzReceive(origin, guid, message, address(0), "") {
    // delivered successfully
} catch {
    bytes32 payloadHash = keccak256(abi.encode(guid, message));
    _inboundPayloadHashes[receiverAddr][srcEid][sender][nonce] = payloadHash;
    emit InboundPayloadStored(receiverAddr, srcEid, sender, nonce, payloadHash);
}
```
Because no explicit gas stipend is passed, `lzReceive` is forwarded ~63/64 of whatever gas is left in `onAccept`'s frame. If the callee (the OApp at `receiverAddr`) consumes gas up to the point where the returned 1/64 remainder is insufficient to execute the `catch` block's two cold `SSTORE`s and `emit`, the resulting out-of-gas condition is unrecoverable and bubbles out of `onAccept` entirely — the `try/catch` never gets the chance to run its recovery logic. This reverts the whole `onAccept` call, undoing the nonce write at line 382 that the code explicitly relies on to survive `lzReceive` failures.

`receiverAddr` is fully attacker/user controlled: `send()` lets any caller set `_params.receiver` to an arbitrary address, which is encoded verbatim into the ISMP body and later decoded as `receiverAddr` in `onAccept` with no validation: [2](#0-1) 

The outer dispatch path in `EvmHost.dispatchIncoming` treats a reverted `onAccept` as a normal retryable failure by deleting the request receipt: [3](#0-2) 
but this only re-enables re-submission of the same POST request — it does not fix the underlying nonce desynchronization inside `HyperbridgeLzEndpoint`, since every retry re-enters `onAccept`, re-checks `expectedNonce`, and re-attempts the same gas-bounded delivery to the same griefing contract.

### Impact Explanation
Because the nonce increment is rolled back on every attempt that OOG-fails inside the `catch`, the `(receiverAddr, srcEid, sender)` channel's expected nonce never advances past the griefed message. Every subsequent legitimate message from that `sender`/`srcEid` destined for that `receiverAddr` will fail the `InvalidNonce` check forever, since the channel can never re-synchronize (there is no permissionless "skip"/"clear" path that operates on an un-advanced nonce — `retryPayload`/`nilify`/`skip` all key off an already-stored payload hash, which is never written when the whole call reverts). This is a logic-level state-corruption bug (the "corrupted value" is the never-updated `_inboundNonce[receiverAddr][srcEid][sender]` slot), not a generic gas-cost DoS: it permanently and unrecoverably breaks a specific inbound message channel, directly undermining the "one-time receipt handling" invariant the code comment says must be preserved.

### Likelihood Explanation
Exploitation requires only a normal, permissionless `send()` call with an attacker-chosen `receiver`, relayed through the standard, honest ISMP proof-verification pipeline — no malicious relayer, prover, or admin is needed. The attacker fully controls the receiving contract's gas consumption pattern, so the 1/64-gas-remainder griefing technique is reliably reproducible (it does not depend on guessing exact gas values, since the ratio-based reservation defeats any fixed gas limit chosen by the relayer submitting the batch).

### Recommendation
Reserve a fixed minimum gas amount for the `catch` recovery path before making the external call, e.g.:
```solidity
try ILayerZeroReceiver(receiverAddr).lzReceive{gas: gasleft() - RESERVE_GAS}(
    origin, guid, message, address(0), ""
) {
    // delivered successfully
} catch {
    bytes32 payloadHash = keccak256(abi.encode(guid, message));
    _inboundPayloadHashes[receiverAddr][srcEid][sender][nonce] = payloadHash;
    emit InboundPayloadStored(receiverAddr, srcEid, sender, nonce, payloadHash);
}
```
where `RESERVE_GAS` comfortably covers two cold `SSTORE`s and an event (e.g. ≥ 30,000 gas), matching the fix recommended for the analogous `NonblockingLzApp._blockingLzReceive` pattern. Additionally, consider using a low-level `call` with `excessivelySafeCall`-style bounded return-data handling instead of `try/catch` so gas accounting is explicit and auditable.

### Proof of Concept
1. Attacker deploys `Griefer` on the destination chain implementing `ILayerZeroReceiver.lzReceive` such that it consumes gas proportional to `gasleft()` at entry (e.g., a loop bounded by `gasleft() > threshold`) and then reverts, tuned so the 1/64 gas remainder returned to `onAccept` is below the cost of the `catch` block's writes.
2. Attacker calls `send()` on the source-chain `HyperbridgeLzEndpoint` with `_params.receiver = address(Griefer)` and any `_params.dstEid`/message.
3. A normal relayer submits the ISMP proof; `EvmHost.dispatchIncoming` calls `HyperbridgeLzEndpoint.onAccept`.
4. `onAccept` writes `_inboundNonce[Griefer][srcEid][sender] = nonce` (line 382), then calls `Griefer.lzReceive` inside `try`.
5. `Griefer` consumes gas per its design and reverts; the `catch` block's `SSTORE`/`emit` run out of gas, causing an OOG revert of the whole `onAccept` call — including the nonce write from step 4.
6. `EvmHost.dispatchIncoming` sees `onAccept` failed and deletes `_requestReceipts[commitment]`, allowing retry — but every retry repeats steps 4-5 identically since the gas-ratio-based griefing scales with whatever gas is supplied. The `(Griefer, srcEid, sender)` channel's nonce never advances, permanently blocking all future messages on that channel.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L262-285)
```text
    function send(
        MessagingParams calldata _params,
        address /* _refundAddress */
    ) external payable override whenNotPaused returns (MessagingReceipt memory) {
        bytes memory dest = _eidToStateMachine[_params.dstEid];
        if (dest.length == 0) revert UnknownEid(_params.dstEid);

        // Track nonce
        uint64 nonce = ++_outboundNonce[msg.sender][_params.dstEid][_params.receiver];

        // Compute globally unique identifier
        bytes32 guid = keccak256(
            abi.encodePacked(nonce, _eid, bytes32(uint256(uint160(msg.sender))), _params.dstEid, _params.receiver)
        );

        // Encode the LZ message into the ISMP body
        bytes memory body = abi.encode(
            guid,
            _eid, // srcEid
            bytes32(uint256(uint160(msg.sender))), // sender
            nonce,
            _params.receiver, // receiver OApp on dest
            _params.message
        );
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L375-396)
```text
        // Validate and advance the nonce. The nonce is committed BEFORE (and independently of)
        // OApp execution: a reverting `lzReceive` must not roll back this write. Otherwise the
        // message would be retried forever at the same nonce and every later nonce would be
        // permanently rejected, bricking the (receiver, srcEid, sender) channel.
        address receiverAddr = address(uint160(uint256(receiver)));
        uint64 expectedNonce = _inboundNonce[receiverAddr][srcEid][sender] + 1;
        if (nonce != expectedNonce) revert InvalidNonce(expectedNonce, nonce);
        _inboundNonce[receiverAddr][srcEid][sender] = nonce;

        // Deliver to the OApp. Isolate the external call so a deterministic revert (zero
        // recipient, over-cap mint, blocklisted recipient, malformed payload, paused OApp, etc.)
        // does not revert `onAccept`. On failure the payload is retained for later retry/recovery
        // via retryPayload/clear/skip/nilify/burn.
        Origin memory origin = Origin({srcEid: srcEid, sender: sender, nonce: nonce});
        try ILayerZeroReceiver(receiverAddr).lzReceive(origin, guid, message, address(0), "") {
            // delivered successfully
        } catch {
            bytes32 payloadHash = keccak256(abi.encode(guid, message));
            _inboundPayloadHashes[receiverAddr][srcEid][sender][nonce] = payloadHash;
            emit InboundPayloadStored(receiverAddr, srcEid, sender, nonce, payloadHash);
        }
    }
```

**File:** evm/src/core/EvmHost.sol (L794-818)
```text
    function dispatchIncoming(PostRequest memory request, address relayer) external restrict(_hostParams.handler) {
        address destination = _bytesToAddress(request.to);
        uint256 size;
        assembly {
            size := extcodesize(destination)
        }
        if (size == 0) {
            // instead of reverting the entire batch, early return here.
            return;
        }

        // replay protection
        bytes32 commitment = request.hash();
        _requestReceipts[commitment] = relayer;

        (bool success,) = address(destination)
            .call(abi.encodeWithSelector(IApp.onAccept.selector, IncomingPostRequest(request, relayer)));

        if (!success) {
            // so that it can be retried
            delete _requestReceipts[commitment];
            return;
        }
        emit PostRequestHandled({commitment: commitment, relayer: relayer});
    }
```
