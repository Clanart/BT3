### Title
`message` Payload Silently Dropped for ERC1155 `finTransfer`, Enabling Permanent Token Lock — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.finTransfer` accepts a MPC-signed `payload.message` field that is verified as part of the signature, but when the transfer path resolves to an ERC1155 token, the `data` argument passed to `IERC1155.safeTransferFrom` is hardcoded to `""` instead of `payload.message`. A recipient contract whose `onERC1155Received` hook requires non-empty data will always revert, making the source-chain ERC1155 tokens permanently unrecoverable.

### Finding Description

`TransferMessagePayload` carries a `bytes message` field: [1](#0-0) 

This field is Borsh-encoded and included in the MPC signature check inside `finTransfer`: [2](#0-1) 

After the signature is verified, the ERC1155 branch executes: [3](#0-2) 

The `data` argument is hardcoded `""`. The signed `payload.message` is never forwarded to the recipient's `onERC1155Received` hook.

By contrast, the bridge-token branch does forward the message when it is non-empty: [4](#0-3) 

Additionally, even in the bridge-token path, `BridgeToken.mint(address, uint256, bytes)` silently discards the `bytes` argument: [5](#0-4) 

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock.**

When a user initiates an ERC1155 bridge transfer with a non-empty `message` to a contract recipient whose `onERC1155Received` requires specific `data` (e.g., a DeFi vault that uses the data field to route deposits), every `finTransfer` call will revert inside `safeTransferFrom`. Because the `data` argument is hardcoded to `""` and cannot be changed by the relayer, no retry can ever succeed. The ERC1155 tokens locked in the bridge contract on the source chain have no admin-recovery path visible in the contract, making the lock permanent.

### Likelihood Explanation

ERC1155 receiver contracts that inspect the `data` parameter in `onERC1155Received` are common in DeFi (e.g., vaults, AMMs, staking contracts). Any user who bridges ERC1155 tokens with a message to such a contract triggers this path. The entry point (`finTransfer`) is public and callable by any relayer once the MPC signature is obtained, so no privileged access is required.

### Recommendation

Replace the hardcoded `""` with `payload.message` in the ERC1155 branch:

```solidity
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this),
    payload.recipient,
    multiToken.tokenId,
    payload.amount,
    payload.message   // forward the signed message
);
```

Also update `BridgeToken.mint(address, uint256, bytes)` to actually use the `bytes` argument (e.g., via an `IERC1363`-style callback or a documented no-op with a clear comment), so the interface contract matches the implementation.

### Proof of Concept

1. User calls `initTransfer` (or the ERC1155 variant) on the source chain, locking ERC1155 tokens in the bridge and specifying a non-empty `message` targeting a DeFi vault contract as `recipient`.
2. MPC signs a `TransferMessagePayload` that includes the `message` field — the signature covers it at lines 305-307.
3. Relayer calls `finTransfer` on the destination chain with the signed payload.
4. The ERC1155 branch executes `safeTransferFrom(..., "")` — the vault's `onERC1155Received` receives empty `data`, cannot determine the deposit route, and reverts.
5. The entire transaction reverts (nonce not consumed), but the source-chain tokens remain locked.
6. Every subsequent retry by any relayer produces the same revert because `""` is hardcoded — no relayer-controlled parameter can change it.
7. With no admin-recovery function for ERC1155 tokens in `OmniBridge.sol`, the tokens are permanently frozen. [3](#0-2) [5](#0-4)

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
