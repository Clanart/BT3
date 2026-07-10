### Title
Zero-byte `message` in `finTransfer` forces 3-arg `HyperliquedBridgeToken.mint`, permanently routing tokens to `_systemAddress` instead of recipient — (`evm/src/omni-bridge/contracts/OmniBridge.sol` / `evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`OmniBridge.finTransfer` selects between the 2-arg and 3-arg `IBridgeToken.mint` overloads solely on `payload.message.length == 0`. `HyperliquedBridgeToken`'s 3-arg mint mints tokens to the recipient and then immediately transfers the full amount to `_systemAddress` via `_update`. Any attacker who initiates a cross-chain transfer with a non-empty but semantically empty message (e.g., a single `0x00` byte) will receive a valid MPC signature covering that payload, and when `finTransfer` executes on EVM, the recipient gets zero tokens while `_systemAddress` absorbs the entire minted amount.

---

### Finding Description

**Routing logic in `OmniBridge.finTransfer`:** [1](#0-0) 

The branch condition is purely `payload.message.length == 0`. A single `0x00` byte has `length == 1`, so the 3-arg path is taken.

**`HyperliquedBridgeToken` 3-arg mint:** [2](#0-1) 

`_mint(account, value)` creates `value` tokens credited to `account`. The immediately following `_update(account, _systemAddress, value)` is the standard ERC-20 internal transfer, moving all `value` tokens from `account` to `_systemAddress`. Net result: recipient balance = 0, `_systemAddress` balance += `value`.

The contract's own NatSpec confirms this is the HyperCore-specific path: [3](#0-2) 

**The `message` field is attacker-controlled and MPC-signed as-is.** The Borsh encoding in `finTransfer` includes the raw message bytes: [4](#0-3) 

When the attacker submits `message = 0x00` on the source chain, the MPC signs a payload that includes `Borsh.encodeBytes(0x00)`. The signature is valid; no forgery is required.

---

### Impact Explanation

Tokens are permanently misdirected. The source-chain tokens are burned/locked. On the destination chain, `_systemAddress` receives the minted tokens instead of the intended recipient. The recipient receives nothing. The bridge's invariant — that a finalized transfer delivers `amount` tokens to `recipient` — is broken. The misdirected tokens inflate the HyperCore pool at `_systemAddress`, corrupting the accounting model that `coreReceiveWithData` depends on (it assumes `_systemAddress` balance mirrors only legitimately HyperCore-bound tokens).

---

### Likelihood Explanation

The attack requires no privilege. Any user who can call `initTransfer` on any source chain (NEAR, EVM, Solana, StarkNet) with a non-empty `message` field can trigger this. The `message` parameter is a plain `string calldata` / `bytes` with no validation: [5](#0-4) 

The attacker spends their own tokens to misdirect value to `_systemAddress`. This is a griefing/misdirection attack rather than direct theft by the attacker, but it constitutes permanent loss for the recipient and accounting corruption for the bridge.

---

### Recommendation

Replace the `length > 0` gate with a semantic check. The 3-arg (HyperCore) path should only be taken when the message carries actual routing content. Options:

1. **Require a non-zero first byte** as a discriminator (e.g., `payload.message.length > 0 && payload.message[0] != 0x00`).
2. **Use a dedicated boolean flag** in `TransferMessagePayload` (e.g., `bool hyperCoreMint`) rather than overloading the `message` field for routing.
3. **Validate in `HyperliquedBridgeToken.mint`** that the `bytes message` argument is non-empty and structurally valid before executing the `_update` to `_systemAddress`.

---

### Proof of Concept

1. Attacker calls `initTransfer` on source chain with `message = "\x00"` (one zero byte), `recipient = victim_address`, `amount = N`.
2. Bridge MPC signs the payload (message is included verbatim in Borsh encoding).
3. Attacker (or relayer) calls `OmniBridge.finTransfer(signature, payload)` on EVM.
4. `payload.message.length == 1 > 0` → 3-arg `IBridgeToken(hlToken).mint(victim, N, "\x00")` is dispatched.
5. Inside `HyperliquedBridgeToken.mint`: `_mint(victim, N)` then `_update(victim, _systemAddress, N)`.
6. Assert: `hlToken.balanceOf(victim) == 0`. Assert: `hlToken.balanceOf(_systemAddress) == N`.

The test suite already confirms the 3-arg mint routes balance to `_systemAddress` even with an empty `bytes` argument (`"0x"`): [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L305-308)
```text
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
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

**File:** evm/src/omni-bridge/contracts/HlBridgeToken.sol (L29-31)
```text
/// @notice Hyperliquid-specific BridgeToken with two mint paths:
/// - 2-arg mint(address, uint256): mints on HyperEVM (tokens go directly to user)
/// - 3-arg mint(address, uint256, bytes): mints on HyperCore (includes _update to system address for spot-balance tracking)
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

**File:** evm/tests/HlBridgeToken.ts (L75-79)
```typescript
    it("mints to account then routes balance to system address", async () => {
      await token.connect(adminAccount)["mint(address,uint256,bytes)"](user1.address, 1000, "0x")
      expect(await token.balanceOf(user1.address)).to.equal(0n)
      expect(await token.balanceOf(SYSTEM_ADDRESS)).to.equal(1000n)
    })
```
