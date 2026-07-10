### Title
3-arg `mint` Triggered by Attacker-Controlled `message` Field Routes All Minted Tokens to `_systemAddress` Instead of Recipient — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`OmniBridge.finTransfer` branches into the 3-arg `IBridgeToken.mint` path whenever `payload.message.length > 0`. For `HyperliquedBridgeToken`, that 3-arg `mint` calls `_mint(account, value)` immediately followed by `_update(account, _systemAddress, value)`, transferring the entire minted balance to `_systemAddress`. Because the `message` field originates from the attacker's `initTransfer` call on the source chain and is faithfully relayed by the NEAR bridge into the MPC-signed payload, any unprivileged caller can force this path for any `HyperliquedBridgeToken` transfer, leaving the intended recipient with zero tokens.

---

### Finding Description

**Root cause 1 — `OmniBridge.finTransfer` branching condition:** [1](#0-0) 

```solidity
} else if (isBridgeToken[payload.tokenAddress]) {
    if (payload.message.length == 0) {
        IBridgeToken(payload.tokenAddress).mint(payload.recipient, payload.amount);
    } else {
        IBridgeToken(payload.tokenAddress).mint(payload.recipient, payload.amount, payload.message);
    }
}
```

The sole condition for selecting the 3-arg mint is `payload.message.length > 0`. There is no check that the message encodes a legitimate HyperCore routing intent, no allowlist of valid message formats, and no restriction on who may supply a non-empty message.

**Root cause 2 — `HyperliquedBridgeToken.mint` (3-arg) immediately re-routes all tokens:** [2](#0-1) 

```solidity
function mint(address account, uint256 value, bytes memory) external override onlyOwner {
    _mint(account, value);
    _update(account, _systemAddress, value);
}
```

`_mint` credits `account` with `value` tokens. `_update(account, _systemAddress, value)` immediately debits the same `value` from `account` and credits `_systemAddress`. The net effect on `account` is zero; all tokens land at `_systemAddress`.

**Root cause 3 — `message` is fully attacker-controlled:** [3](#0-2) 

`initTransfer` accepts `string calldata message` from `msg.sender` with no validation. This value is emitted in the `InitTransfer` event, picked up by the NEAR bridge, included verbatim in the `TransferMessagePayload`, and covered by the MPC signature. The MPC signs whatever the NEAR bridge constructs from the on-chain event — it does not validate the semantic meaning of `message`.

**Attack path:**

1. Attacker calls `initTransfer(hlToken, amount, fee, 0, "victim.near", "x")` on any source chain — `"x"` is a single non-empty byte.
2. NEAR bridge observes the `InitTransfer` event and constructs a `TransferMessagePayload` with `message = "x"`.
3. MPC signs the payload; relayer calls `OmniBridge.finTransfer` with the valid signature.
4. `finTransfer` sees `isBridgeToken[hlToken] == true` and `payload.message.length == 1 > 0`, so it calls `HyperliquedBridgeToken.mint(recipient, amount, "x")`.
5. `_mint(recipient, amount)` runs, then `_update(recipient, _systemAddress, amount)` runs — recipient balance is zero, `_systemAddress` balance increases by `amount`.
6. Recipient receives nothing; their bridged assets are permanently misdirected.

---

### Impact Explanation

**Critical — direct theft/irrecoverable misdirection of bridged assets.**

Every `HyperliquedBridgeToken` transfer that carries a non-empty `message` (attacker-supplied or even legitimately supplied by a user who wants to pass data) will have 100% of its minted tokens routed to `_systemAddress` instead of the intended recipient. The recipient's balance after `finTransfer` is exactly zero. The tokens are not recoverable by the recipient through any public bridge function; only the Hyperliquid system address can subsequently release them via `coreReceiveWithData`. [4](#0-3) 

---

### Likelihood Explanation

**High.** The attacker needs no special role, no leaked key, and no colluding MPC signers. The only precondition is that a `HyperliquedBridgeToken` is registered as `isBridgeToken` — a normal production state. The attacker simply passes a single non-empty byte as `message` in `initTransfer`. The NEAR bridge and MPC will process it identically to any legitimate transfer. The exploit is deterministic and locally testable.

---

### Recommendation

Two complementary fixes are needed:

1. **In `OmniBridge.finTransfer`:** Do not use `message.length > 0` as the proxy for "HyperCore transfer." Either introduce a dedicated boolean flag in the payload (e.g., `isHyperCore`), or require the message to match a specific well-known prefix/format before selecting the 3-arg mint path.

2. **In `HyperliquedBridgeToken.mint` (3-arg):** Add an explicit guard so the function can only be called when the transfer is genuinely destined for HyperCore. If the message is not a valid HyperCore routing instruction, revert rather than silently rerouting tokens to `_systemAddress`. [5](#0-4) [2](#0-1) 

---

### Proof of Concept

```solidity
// Setup: HyperliquedBridgeToken deployed, owned by OmniBridge, registered as isBridgeToken.
// _systemAddress = address(0xSYS)

// 1. Attacker initiates transfer on source chain with non-empty message
omniBridge.initTransfer(address(hlToken), 1000e18, 0, 0, "victim.near", "x");

// 2. NEAR MPC signs TransferMessagePayload{..., message: bytes("x")}
// 3. Relayer calls finTransfer with valid MPC signature
omniBridge.finTransfer(mpcSignature, TransferMessagePayload{
    tokenAddress: address(hlToken),
    amount: 1000e18,
    recipient: victim,
    message: bytes("x"),   // non-empty → 3-arg mint selected
    ...
});

// 4. Inside HyperliquedBridgeToken.mint(victim, 1000e18, bytes("x")):
//    _mint(victim, 1000e18)          → victim.balance = 1000e18
//    _update(victim, _systemAddress, 1000e18) → victim.balance = 0, _systemAddress.balance += 1000e18

assert(hlToken.balanceOf(victim) == 0);           // victim gets nothing
assert(hlToken.balanceOf(_systemAddress) == 1000e18); // tokens at system address
```

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

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L106-114)
```text
    function coreReceiveWithData(
        address from,
        bytes32 /*destinationRecipient*/,
        uint32 /*destinationChainId*/,
        uint256 amount,
        uint64 /*coreNonce*/,
        bytes calldata data
    ) external override {
        if (msg.sender != _systemAddress) revert NotSystemAddress();
```
