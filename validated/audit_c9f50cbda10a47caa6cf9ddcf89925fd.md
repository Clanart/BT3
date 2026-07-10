Let me look at the `IBridgeToken` interface and `BridgeToken` base to understand the full mint interface.

### Title
Non-Empty `message` Field Causes `HyperliquedBridgeToken` 3-arg `mint` to Route All Tokens to `_systemAddress` Instead of Recipient — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`OmniBridge.finTransfer` selects the 3-arg `IBridgeToken.mint(address, uint256, bytes)` path whenever `payload.message.length > 0`. For `HyperliquedBridgeToken`, that overridden function calls `_mint(account, value)` followed immediately by `_update(account, _systemAddress, value)`, transferring the entire minted balance to `_systemAddress`. Because the `message` field is freely settable by any caller of `initTransfer`, an unprivileged attacker can force any `finTransfer` targeting a `HyperliquedBridgeToken` to credit zero tokens to the intended recipient.

---

### Finding Description

**Step 1 — Attacker-controlled `message` field.**
`OmniBridge.initTransfer` accepts `message` as a free `string calldata` parameter with no restrictions on content or length. [1](#0-0) 

**Step 2 — MPC signs the full payload including `message`.**
`finTransfer` Borsh-encodes the message bytes into the signed digest. The MPC signs whatever was submitted on the source chain; it does not validate whether the message is meaningful for the destination token type. [2](#0-1) 

**Step 3 — `finTransfer` branches on `message.length > 0`.**
The only guard that selects between the 2-arg and 3-arg mint is a length check. There is no check on whether the token is a `HyperliquedBridgeToken` or a plain `BridgeToken`. [3](#0-2) 

**Step 4 — `HyperliquedBridgeToken.mint(address, uint256, bytes)` routes tokens to `_systemAddress`.**
The override mints `value` to `account` and then immediately transfers the full amount away to `_systemAddress`. The `bytes` argument is ignored entirely (named `memory` with no identifier). Net effect: `account.balanceOf == 0`, `_systemAddress.balanceOf += value`. [4](#0-3) 

**Contrast with the base `BridgeToken` override**, which simply calls `_mint(account, value)` and stops — no `_update` to `_systemAddress`. [5](#0-4) 

The 3-arg path in `HyperliquedBridgeToken` is architecturally intended only for HyperCore-bound mints (where tokens are deliberately parked at `_systemAddress` to mirror HyperCore spot balances). Triggering it via a plain cross-chain transfer with a non-empty message is an unintended and destructive misuse.

---

### Impact Explanation

Every `finTransfer` that targets a `HyperliquedBridgeToken` recipient and carries a non-empty `message` field will credit **zero** tokens to `payload.recipient` and silently deposit the full `payload.amount` into `_systemAddress`. The recipient permanently loses their bridged assets. This violates the core bridge invariant that `finTransfer` must credit exactly `payload.amount` tokens to `payload.recipient`.

Tokens parked at `_systemAddress` are the standing HyperCore pool; they are not recoverable by the original recipient through any standard bridge path. Depending on Hyperliquid protocol internals, a party with a HyperCore account could claim them via `coreReceiveWithData`, making this a direct theft scenario in addition to a permanent lock.

**Impact category:** Critical — direct unauthorized routing of bridged assets away from the intended recipient; permanent loss of user funds.

---

### Likelihood Explanation

- The `message` field requires no privilege to set; any `initTransfer` caller can supply `message = "x"` (one byte suffices).
- The MPC signs the payload as-is; it has no semantic awareness of the destination token type.
- The relayer submits any validly signed payload.
- No on-chain guard in `finTransfer` prevents the 3-arg path from being taken for `HyperliquedBridgeToken`.
- The attack is deterministic and locally reproducible with a single transaction sequence.

**Likelihood: High.**

---

### Recommendation

Two complementary fixes are needed:

1. **In `HyperliquedBridgeToken.mint(address, uint256, bytes)`**: Guard against being called from a plain bridge finalization context. One approach is to only execute the `_update` to `_systemAddress` when the `bytes` argument encodes a recognized HyperCore routing marker, and revert (or fall back to plain `_mint`) otherwise.

2. **In `OmniBridge.finTransfer`**: Do not use `message.length > 0` as the sole selector for the 3-arg mint path. Either (a) introduce a per-token flag (e.g., `isHyperCoreBridgeToken`) that gates the 3-arg path, or (b) always call the 2-arg mint for standard finalization and reserve the 3-arg path for a separate, explicitly HyperCore-targeted entry point.

---

### Proof of Concept

```solidity
// 1. Deploy HyperliquedBridgeToken owned by OmniBridge, registered as isBridgeToken.
// 2. Attacker calls initTransfer on source chain:
//      initTransfer(tokenAddress, 1e18, 0, 0, "victim.near", "x")
//      (message = "x", length = 1 > 0)
// 3. MPC signs TransferMessagePayload{..., message: bytes("x")}.
// 4. Relayer calls OmniBridge.finTransfer(sig, payload) on HyperEVM.
// 5. finTransfer takes the isBridgeToken branch, message.length > 0 → 3-arg mint.
// 6. HyperliquedBridgeToken.mint(recipient, 1e18, bytes("x")):
//      _mint(recipient, 1e18)          // recipient balance = 1e18
//      _update(recipient, _systemAddress, 1e18) // recipient balance = 0
// 7. Assert:
//      token.balanceOf(recipient)     == 0
//      token.balanceOf(_systemAddress) == 1e18
```

The preconditions are entirely reachable on a local fork: deploy the contracts, register the token, and execute the five-step sequence above.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L305-313)
```text
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
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
