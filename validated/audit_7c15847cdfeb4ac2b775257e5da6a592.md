### Title
`payload.message` Silently Dropped in ERC1155 `finTransfer`, Causing Irrecoverable Fund Lock for Contract Recipients - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

In `OmniBridge.finTransfer`, when the destination asset is an ERC1155 token, the `payload.message` field — which is part of the MPC-signed payload and can be non-empty — is silently discarded. A hardcoded empty `""` is passed as the `data` argument to `IERC1155.safeTransferFrom`. This is the direct analog of M-12: a "safe" transfer that drops the `_data` argument. For ERC20 bridge tokens the message is correctly forwarded; for ERC1155 it is not. If the contract recipient's `onERC1155Received` hook requires non-empty data (e.g., to authorize or route the transfer), every `finTransfer` attempt will revert, permanently locking the user's funds that were already burned/locked on the NEAR side.

---

### Finding Description

`TransferMessagePayload` carries a `bytes message` field: [1](#0-0) 

This field is included in the Borsh-encoded, MPC-signed blob verified inside `finTransfer`: [2](#0-1) 

After signature verification, the ERC1155 branch calls `safeTransferFrom` with a hardcoded empty byte string, discarding `payload.message` entirely: [3](#0-2) 

By contrast, the ERC20 bridge-token branch correctly forwards the message to `mint`: [4](#0-3) 

The inconsistency is structural: the message is authenticated by the MPC signature and is part of the protocol's cross-chain payload, yet it is unconditionally thrown away for ERC1155 assets.

---

### Impact Explanation

When `payload.recipient` is a smart contract whose `onERC1155Received` hook requires non-empty `data` (a common pattern in cross-chain DeFi integrations — e.g., to verify origin, route funds, or execute a follow-on action), the hook will revert. Because the nonce is marked used before the transfer executes: [5](#0-4) 

…the entire transaction reverts, so the nonce is not permanently consumed and the relayer can retry. However, the retry will always produce the same empty `data` and the same revert. The NEAR-side tokens were already burned or locked when the user called `initTransfer`. Without a new MPC-signed payload (trusted operator action), the funds are irrecoverably locked.

Impact class: **Permanent freezing / irrecoverable lock of user funds in the bridge flow.**

---

### Likelihood Explanation

- The `message` field is a first-class, authenticated part of the protocol payload, explicitly designed for cross-chain calldata.
- Cross-chain DeFi protocols (vaults, DEX routers, lending protocols) routinely use the ERC1155 `data` parameter to authorize or route incoming transfers.
- Any user who bridges ERC1155 tokens to such a contract recipient with a non-empty message will trigger this path.
- No privileged access is required; any unprivileged bridge user initiating an ERC1155 transfer to a contract recipient is sufficient.

Likelihood: **Medium** — the scenario is realistic for any cross-chain DeFi integration using ERC1155 with calldata routing.

---

### Recommendation

Pass `payload.message` as the `data` argument to `safeTransferFrom` instead of the hardcoded empty string:

```solidity
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this),
    payload.recipient,
    multiToken.tokenId,
    payload.amount,
    payload.message   // ← forward the authenticated message
);
```

This mirrors the treatment of `payload.message` in the ERC20 bridge-token branch and ensures the recipient contract receives the data the sender intended.

---

### Proof of Concept

1. On NEAR, a user calls the bridge to transfer ERC1155 token `T` (tokenId `42`) to a contract `RecipientVault` on EVM, including a non-empty `message` (e.g., ABI-encoded routing instructions).
2. NEAR side burns/locks the tokens and the MPC signs a `TransferMessagePayload` with `message = <routing_bytes>`.
3. A relayer calls `OmniBridge.finTransfer(signature, payload)`.
4. The bridge verifies the signature (passes), then executes:
   ```solidity
   IERC1155(multiToken.tokenAddress).safeTransferFrom(
       address(this), RecipientVault, 42, amount, ""
   );
   ```
5. `RecipientVault.onERC1155Received` receives `data = ""`, fails its routing check, and reverts.
6. The entire `finTransfer` transaction reverts; the nonce is not consumed.
7. Every subsequent relay attempt produces the same empty `data` and the same revert.
8. The user's ERC1155 tokens are permanently locked on the NEAR side with no recovery path absent a new MPC-signed payload. [3](#0-2)

### Citations

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L305-308)
```text
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L323-330)
```text
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L343-349)
```text
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
```
