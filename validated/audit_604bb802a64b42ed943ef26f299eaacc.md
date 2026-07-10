The key code to analyze is the `mint(address, uint256, bytes)` override in `HyperliquedBridgeToken` and the dispatch logic in `finTransfer`. Let me trace the exact execution path.

**`finTransfer` dispatch (lines 337–349):** [1](#0-0) 

**`HyperliquedBridgeToken.mint` 3-arg override (lines 76–83):** [2](#0-1) 

**`message` is user-controlled at source chain via `initTransfer` (line 379):** [3](#0-2) 

---

### Title
3-arg `mint` in `HyperliquedBridgeToken` immediately drains minted tokens to `_systemAddress` when `message.length > 0` — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

### Summary

`HyperliquedBridgeToken.mint(address, uint256, bytes)` calls `_mint(account, value)` followed immediately by `_update(account, _systemAddress, value)`. The net effect is zero tokens for the recipient and all minted tokens parked at `_systemAddress`. This 3-arg path is taken by `OmniBridge.finTransfer` whenever `payload.message.length > 0`. Because `message` is a free `string calldata` parameter in `initTransfer` on the source chain, any user can trigger this path by including any non-empty message in their cross-chain transfer.

### Finding Description

In `OmniBridge.finTransfer`, when `isBridgeToken[payload.tokenAddress]` is true and `payload.message.length > 0`, the bridge calls:

```solidity
IBridgeToken(payload.tokenAddress).mint(
    payload.recipient,
    payload.amount,
    payload.message   // attacker-controlled bytes
);
```

`HyperliquedBridgeToken` overrides this as:

```solidity
function mint(address account, uint256 value, bytes memory) external override onlyOwner {
    _mint(account, value);              // recipient balance += value
    _update(account, _systemAddress, value); // recipient balance -= value, _systemAddress += value
}
``` [2](#0-1) 

`_mint` increases the recipient's balance by `value`. `_update` is the raw ERC-20 transfer hook — it immediately moves those same `value` tokens from `account` to `_systemAddress`. The recipient's net balance change is **zero**; `_systemAddress` receives all minted tokens.

The `message` field is set by the originating user on the source chain via `initTransfer`'s `string calldata message` parameter — it is entirely user-controlled and is included verbatim in the MPC-signed payload. Any non-empty byte string (even a single `0x00` byte) is sufficient to select the 3-arg path. [4](#0-3) 

### Impact Explanation

Every cross-chain transfer to a `HyperliquedBridgeToken` address that carries a non-empty `message` results in the recipient receiving **zero tokens** while the full minted amount is locked at `_systemAddress`. There is no recovery path: `_systemAddress` is a Hyperliquid system contract, not a user-controlled address. Funds are permanently irrecoverable for the recipient.

This matches: **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

### Likelihood Explanation

- `HyperliquedBridgeToken` is a production contract in `evm/src/omni-bridge/contracts/`.
- The `message` field is a documented, user-facing parameter of `initTransfer` — users legitimately pass messages for memo or cross-chain callback purposes.
- Any user who includes a non-empty message in a transfer to a `HyperliquedBridgeToken` destination will silently lose their funds. No privileged access, no key compromise, no colluding MPC signers required — the MPC signs the payload exactly as submitted by the user.
- The relayer submitting `finTransfer` is irrelevant; the vulnerability is triggered by the user's own source-chain `initTransfer` call.

### Recommendation

Fix `HyperliquedBridgeToken.mint(address, uint256, bytes)` so that it does **not** call `_update(account, _systemAddress, value)` after `_mint`. The `_update` to `_systemAddress` is the HyperCore accounting hook and should only be invoked when tokens are genuinely being routed to HyperCore, not on every `finTransfer` with a non-empty message. One correct approach: mint directly to `_systemAddress` (not `account`) when the intent is HyperCore delivery, or remove the `_update` call entirely from the 3-arg mint and handle HyperCore accounting separately.

Additionally, `OmniBridge` should not use `message.length > 0` as the sole selector for the 3-arg mint path without understanding the token-specific semantics of that overload.

### Proof of Concept

1. Deploy `HyperliquedBridgeToken` and register it as a bridge token via `addCustomToken`.
2. On the source chain, call `initTransfer` with `message = "x"` (any non-empty string) targeting the `HyperliquedBridgeToken` address and `recipient = alice`.
3. The MPC signs the payload (including the non-empty message).
4. Relayer calls `finTransfer` with the signed payload.
5. `finTransfer` takes the `message.length > 0` branch and calls `HyperliquedBridgeToken.mint(alice, amount, "x")`.
6. `_mint(alice, amount)` → alice balance = amount.
7. `_update(alice, _systemAddress, amount)` → alice balance = 0, `_systemAddress` balance = amount.
8. Assert: `token.balanceOf(alice) == 0` and `token.balanceOf(_systemAddress) == amount`.

Alice's tokens are permanently locked at `_systemAddress`. [2](#0-1) [1](#0-0)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-380)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
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
