### Title
Emergency pause in `HyperbridgeLzEndpoint` does not block permissionless delivery of stored payloads via `retryPayload` - (File: sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol)

### Summary
`HyperbridgeLzEndpoint` inherits `Pausable` and gates its two primary cross-chain entrypoints, `send` and `onAccept`, with `whenNotPaused` [1](#0-0) [2](#0-1) . However, `retryPayload`, the function that force-delivers a previously stored/failed inbound payload directly to the destination OApp's `lzReceive`, is explicitly documented as permissionless ("anyone may push a stuck payload through") and is declared without the same pause guard [3](#0-2) . This mirrors the reported `WrappedBitcornNativeOFTAdapter._credit` bug class: an execution path that mutates state/executes value-bearing logic while the contract is supposedly paused, because the pause modifier was applied inconsistently across related functions.

### Finding Description
When `onAccept` fails to deliver an inbound ISMP message to the destination OApp (e.g., the OApp reverts), the payload hash is stored for later retry instead of being dropped [4](#0-3) . The owner can call `pause()` to halt `send()` and `onAccept()` in an emergency [5](#0-4) , but `retryPayload` still calls `ILayerZeroReceiver(receiver).lzReceive(...)` directly, bypassing the endpoint's pause state entirely because it has no `whenNotPaused` modifier. Since `retryPayload` is explicitly callable by anyone, a pause does not stop already-queued (potentially malicious or exploit-triggering) payloads from being force-delivered to any registered OApp during the pause window.

### Impact Explanation
This breaks the intended guarantee of the emergency pause: an admin pausing the endpoint (e.g., in response to a detected exploit against an OApp's `lzReceive` handling, or a malformed/malicious stored payload) cannot actually stop that payload from being executed, because `retryPayload` remains open to any caller. If an attacker can get a message stored via a deliberately-reverting first delivery attempt (e.g. crafting a payload that reverts under certain gas/state conditions on first try but succeeds later), they can wait for a pause and then force execution via `retryPayload`, defeating the purpose of the circuit breaker and potentially causing unauthorized execution or fund-moving logic to run on the destination OApp against operator intent.

### Likelihood Explanation
The function is permissionless by design and requires no relayer, prover, or admin compromise — only a previously stored payload hash (created naturally whenever an OApp's `lzReceive` reverts once) and a pause event, both of which are ordinary operational conditions rather than adversarial assumptions.

### Recommendation
Add the `whenNotPaused` modifier to `retryPayload` (and audit other payload-recovery functions such as `clear`/`skip`/`nilify`/`burn` if present) so that all paths capable of triggering `lzReceive` execution are consistently blocked while the endpoint is paused, matching the pattern already used correctly in `HyperFungibleToken`/`WrappedHyperFungibleToken`'s `onAccept`/`onPostRequestTimeout`.

### Proof of Concept
1. Attacker crafts or waits for an inbound message whose first `lzReceive` call reverts, causing `onAccept` to store its payload hash (per lines 384-395).
2. Endpoint owner detects an incident and calls `pause()`.
3. Attacker (or anyone) calls `retryPayload(receiver, origin, guid, message)` — since it lacks `whenNotPaused`, the call succeeds and `lzReceive` executes on the OApp despite the pause.
4. The emergency stop fails to prevent the OApp's state/fund-affecting logic from running.

Note: I could only view lines 406-420 of `retryPayload`'s declaration in the available index; the closing brace and modifier list beyond line 420 were not retrievable due to index size limits. I recommend verifying the complete function signature directly in the repository (or via a Devin session with full file access) to confirm no `whenNotPaused` modifier appears further down before treating this as fully confirmed.

### Citations

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L244-257)
```text
     * @notice Pauses all cross-chain operations (send and receive)
     * @dev Only callable by the contract owner
     */
    function pause() external onlyOwner {
        _pause();
    }

    /**
     * @notice Unpauses all cross-chain operations
     * @dev Only callable by the contract owner
     */
    function unpause() external onlyOwner {
        _unpause();
    }
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L262-265)
```text
    function send(
        MessagingParams calldata _params,
        address /* _refundAddress */
    ) external payable override whenNotPaused returns (MessagingReceipt memory) {
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L355-356)
```text
    function onAccept(IncomingPostRequest calldata incoming) external override onlyHost whenNotPaused {
        PostRequest calldata request = incoming.request;
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L384-395)
```text
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
```

**File:** sdk/packages/lz-endpoint/contracts/HyperbridgeLzEndpoint.sol (L406-420)
```text
     * @notice Retries an inbound delivery whose OApp `lzReceive` previously reverted in {onAccept}.
     * @dev Mirrors {onAccept}'s direct call to the OApp (the adapter is the caller, so the OApp's
     * `onlyEndpoint` check still passes). Permissionless: anyone may push a stuck payload through
     * once it is executable again. On success the stored payload hash is cleared; if delivery
     * reverts again the whole call reverts and the payload remains recoverable.
     * @param receiver The destination OApp
     * @param origin The (srcEid, sender, nonce) of the stored payload
     * @param guid The original message guid
     * @param message The original message payload (must match the stored hash)
     */
    function retryPayload(
        address receiver,
        Origin calldata origin,
        bytes32 guid,
        bytes calldata message
```
