### Title
`HlBridgeToken` 3-arg `mint` Unconditionally Routes Recipient Tokens to `_systemAddress` on `finTransfer` with Non-Empty Message — (`evm/src/omni-bridge/contracts/HlBridgeToken.sol`)

---

### Summary

`HlBridgeToken.mint(address, uint256, bytes)` always moves all minted tokens from the intended recipient to `_systemAddress` after minting. `OmniBridge.finTransfer` calls this 3-arg override whenever `payload.message.length != 0`. The result: the EVM recipient receives 0 tokens, the tokens are stranded at `_systemAddress`, and the `FinTransfer` event falsely records a successful delivery of `amount` to `recipient`.

---

### Finding Description

`HlBridgeToken` overrides the 3-arg `mint` from `BridgeToken` to implement the HyperCore standing-pool model:

```solidity
function mint(
    address account,
    uint256 value,
    bytes memory          // ignored
) external override onlyOwner {
    _mint(account, value);
    _update(account, _systemAddress, value);   // ← always drains recipient
}
```

`_mint(account, value)` increases `account`'s balance by `value` and increases `totalSupply`. The immediately following `_update(account, _systemAddress, value)` transfers the entire `value` back out of `account` into `_systemAddress`. After the call, `account` holds 0 new tokens and `_systemAddress` holds `value` additional tokens.

`OmniBridge.finTransfer` dispatches to the 3-arg overload whenever the signed payload carries a non-empty `message`:

```solidity
} else if (isBridgeToken[payload.tokenAddress]) {
    if (payload.message.length == 0) {
        IBridgeToken(payload.tokenAddress).mint(
            payload.recipient, payload.amount
        );
    } else {
        IBridgeToken(payload.tokenAddress).mint(   // ← 3-arg path
            payload.recipient, payload.amount, payload.message
        );
    }
}
```

For every other `BridgeToken`, the 3-arg `mint` simply calls `_mint(account, value)` and ignores the bytes parameter — the recipient keeps the tokens. For `HlBridgeToken` the override silently redirects all tokens to `_systemAddress`.

The `message` field in the signed `TransferMessagePayload` originates from the user's `msg` field in `InitTransferMsg` on the NEAR side. In `sign_transfer` on NEAR:

```rust
let message = DestinationChainMsg::from_json(&transfer_message.msg)
    .and_then(|s| s.destination_msg())
    .unwrap_or_default();
```

Any user who supplies a non-empty, valid `DestinationChainMsg` when initiating a NEAR → HyperEVM transfer (e.g., to trigger a DeFi hook on arrival) will produce a non-empty `message` in the signed payload, routing their tokens to `_systemAddress` instead of to themselves.

---

### Impact Explanation

**Critical — Permanent, irrecoverable loss of user funds in the bridge finalization flow.**

- The EVM recipient receives 0 tokens despite the MPC-signed payload authorizing delivery of `amount`.
- The `FinTransfer` event emits `(originChain, originNonce, tokenAddress, amount, recipient, feeRecipient)` recording a successful delivery that never occurred.
- Tokens stranded at `_systemAddress` are not recoverable by the EVM recipient; `_systemAddress` is the HyperCore protocol system address, not a user-controlled account.
- Bridge collateralization is broken: NEAR-side tokens were burned/locked for the transfer, but the EVM-side recipient received nothing.

---

### Likelihood Explanation

The trigger is a user-controlled field (`msg` in `InitTransferMsg`) that is explicitly supported by the bridge protocol for DeFi-integration use cases (e.g., calling a contract on arrival). Any user who exercises this feature while bridging to an `HlBridgeToken` destination loses their funds. No privileged access, key compromise, or external dependency failure is required — only a standard bridge call with a non-empty message.

---

### Recommendation

Do not override the 3-arg `mint` in `HlBridgeToken` with the HyperCore pool-routing logic. The 3-arg `mint` is the `finTransfer` delivery path for all bridge tokens; it must deliver tokens to `account`. Introduce a separate, dedicated function (e.g., `mintToPool`) for the HyperCore standing-pool case and call it only from the HyperCore-specific code path (`coreReceiveWithData` / NEAR → HyperCore finalization). The 3-arg `mint` override should match the base `BridgeToken` behavior:

```solidity
function mint(
    address account,
    uint256 value,
    bytes memory
) external override onlyOwner {
    _mint(account, value);   // deliver to recipient, no pool redirect
}
```

---

### Proof of Concept

**Setup**: `HlBridgeToken` deployed and registered in `OmniBridge` via `addCustomToken` (sets `isBridgeToken[tokenAddress] = true`).

**Steps**:

1. Alice initiates a transfer from NEAR to HyperEVM, supplying a non-empty `msg` (e.g., a `DestinationChainMsg` targeting a DeFi contract). Amount: 1000 tokens.
2. NEAR MPC signs a `TransferMessagePayload` with `message = <non-empty bytes>`, `recipient = alice_evm`, `amount = 1000`.
3. Relayer calls `OmniBridge.finTransfer(signature, payload)`.
4. `finTransfer` verifies the signature, marks the nonce used, then enters the `isBridgeToken` branch. Because `payload.message.length != 0`, it calls `HlBridgeToken.mint(alice_evm, 1000, payload.message)`.
5. Inside `mint`: `_mint(alice_evm, 1000)` → `alice_evm.balance = 1000`, `totalSupply = 1000`. Then `_update(alice_evm, _systemAddress, 1000)` → `alice_evm.balance = 0`, `_systemAddress.balance += 1000`.
6. `FinTransfer` event emits `amount=1000, recipient=alice_evm`. Alice's actual balance: **0**.
7. Alice's 1000 tokens are permanently stranded at `_systemAddress`.

**Relevant code locations**: [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L359-366)
```text
        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
```
