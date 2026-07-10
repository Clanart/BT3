The exploit path is concrete. Let me trace it precisely through the production code.

**Step 1 — `finTransfer` dispatch**

In `OmniBridge.finTransfer`, when `isBridgeToken[payload.tokenAddress]` is true and `payload.message.length > 0`, the 3-arg mint is dispatched: [1](#0-0) 

**Step 2 — `HyperliquedBridgeToken.mint` (3-arg)** [2](#0-1) 

- `_mint(account, value)` → `account` receives `value` tokens
- `_update(account, _systemAddress, value)` → immediately transfers all `value` tokens from `account` to `_systemAddress`

Net result: `account` (the recipient) holds **0 tokens**; `_systemAddress` holds `value` tokens.

The `bytes memory` parameter is unnamed and completely ignored — any non-empty byte string triggers this path.

**Step 3 — Signature requirement**

The MPC signature covers the full borsh-encoded payload including `payload.message`: [3](#0-2) 

The relayer does not forge the signature. The NEAR-side sender sets the `message` field when initiating a transfer. The MPC signs whatever the NEAR bridge produces. A NEAR user sending any non-empty `message` bytes to an `HlBridgeToken` recipient will produce a legitimately signed payload that triggers this path.

**Step 4 — Token recovery**

Tokens at `_systemAddress` can only be released via `coreReceiveWithData`, which is gated to `msg.sender == _systemAddress`: [4](#0-3) 

There is no mechanism for the original recipient to recover their tokens. The HyperLiquid system address has no obligation to release tokens that arrived via this path (no corresponding HyperCore deposit occurred).

---

### Title
`HyperliquedBridgeToken.mint` 3-arg permanently strands bridged tokens at `_systemAddress` when `finTransfer` is called with a non-empty message — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

### Summary
`HyperliquedBridgeToken.mint(address, uint256, bytes)` mints tokens to `account` and then immediately calls `_update(account, _systemAddress, value)`, transferring the full minted amount away from the recipient. Any `finTransfer` call for an `HlBridgeToken` with a non-empty `message` field triggers this path, delivering zero tokens to the intended recipient and permanently parking the full amount at `_systemAddress`.

### Finding Description
`OmniBridge.finTransfer` dispatches to the 3-arg `IBridgeToken.mint` whenever `isBridgeToken[payload.tokenAddress]` is true and `payload.message.length > 0`. [1](#0-0) 

`HyperliquedBridgeToken` overrides this function as:

```solidity
function mint(address account, uint256 value, bytes memory) external override onlyOwner {
    _mint(account, value);
    _update(account, _systemAddress, value);
}
``` [2](#0-1) 

The `bytes memory` argument is unnamed and ignored. The sequence `_mint` then `_update(account → _systemAddress)` unconditionally moves the entire minted balance to `_systemAddress`, regardless of message content. The recipient's balance after the call is zero.

The base `BridgeToken.mint(address, uint256, bytes)` does not have this problem — it simply calls `_mint(account, value)` and returns: [5](#0-4) 

### Impact Explanation
Any NEAR-side user who initiates a transfer to an `HlBridgeToken` EVM address with a non-empty `message` field will have their tokens permanently stranded at `_systemAddress`. The recipient receives zero tokens. Recovery requires `_systemAddress` (the HyperLiquid system address) to call `coreReceiveWithData` with `ACTION_TRANSFER`, which it will not do because no corresponding HyperCore deposit occurred. This is a permanent, irrecoverable loss of bridged funds.

### Likelihood Explanation
The `message` field is a standard, user-visible parameter in the NEAR-side bridge initiation flow. Any NEAR user who passes a non-empty string (e.g., a memo, a routing hint, or any arbitrary bytes) when bridging to an `HlBridgeToken` address will trigger this. No privileged access, key compromise, or colluding MPC signers are required — the MPC signs the payload as presented, including the message. The relayer submits the legitimately signed payload. The vulnerability fires on every such transfer.

### Recommendation
The 3-arg `mint` in `HyperliquedBridgeToken` should not unconditionally transfer the minted tokens to `_systemAddress`. The `message` bytes should be decoded to determine the intended destination (HyperEVM vs. HyperCore). If the message indicates a HyperEVM delivery, tokens should remain with `account`. If it indicates HyperCore delivery, the current behavior is appropriate. Alternatively, if the 3-arg path is exclusively for HyperCore, `OmniBridge.finTransfer` should not route to it based solely on `message.length > 0` for `HlBridgeToken` addresses — a separate dispatch mechanism (e.g., a dedicated flag or token-type check) should be used.

### Proof of Concept
1. Deploy `HyperliquedBridgeToken` with `_systemAddress = S`.
2. Register it in `OmniBridge` via `addCustomToken` so `isBridgeToken[token] = true`.
3. Obtain a valid MPC signature for a `TransferMessagePayload` with `tokenAddress = token`, `recipient = alice`, `amount = 1000`, `message = 0x01` (any non-empty bytes).
4. Call `OmniBridge.finTransfer(sig, payload)`.
5. Assert: `token.balanceOf(alice) == 0` and `token.balanceOf(S) == 1000`.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L305-311)
```text
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L337-349)
```text
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L76-83)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external override onlyOwner {
        _mint(account, value);
        _update(account, _systemAddress, value);
    }
```

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L113-114)
```text
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```

**File:** evm/src/omni-bridge/contracts/BridgeToken.sol (L54-60)
```text
    function mint(
        address account,
        uint256 value,
        bytes memory
    ) external virtual onlyOwner {
        _mint(account, value);
    }
```
