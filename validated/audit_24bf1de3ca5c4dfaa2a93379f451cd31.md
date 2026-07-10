### Title
ERC-721 Tokens Can Be Permanently Locked in OmniBridge via `initTransfer` Token-Type Confusion — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` accepts any token address without verifying it implements ERC-20. Because ERC-721's `transferFrom(address,address,uint256)` shares the same ABI selector as ERC-20's `transferFrom`, OpenZeppelin's `SafeERC20.safeTransferFrom` succeeds when called on an ERC-721 contract. The ERC-721 token is deposited into the bridge. However, `finTransfer` releases native tokens via `SafeERC20.safeTransfer`, which calls `transfer(address,uint256)` — a function ERC-721 does not implement — causing the release to revert permanently. The ERC-721 token is irrecoverably locked with no on-chain recovery path short of an upgrade.

---

### Finding Description

`initTransfer` in `OmniBridge.sol` handles non-bridge, non-custom tokens with:

```solidity
IERC20(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    amount
);
``` [1](#0-0) 

OpenZeppelin's `SafeERC20.safeTransferFrom` performs a low-level call to `transferFrom(address,address,uint256)`. ERC-721 defines `transferFrom(address from, address to, uint256 tokenId)` with the identical ABI signature. Passing an ERC-721 contract address with `amount = tokenId` causes the ERC-721 token to be transferred into the bridge successfully.

There is no ERC-20 type guard anywhere in `initTransfer`. The function only checks:
- `fee >= amount` (trivially bypassed with `fee = 0`)
- Whether the token is in `customMinters` or `isBridgeToken` (a fresh ERC-721 is in neither) [2](#0-1) 

On the return leg, `finTransfer` dispatches native (non-bridge, non-custom, non-multiToken) tokens via:

```solidity
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [3](#0-2) 

`SafeERC20.safeTransfer` calls `transfer(address,uint256)`. ERC-721 does **not** implement `transfer(address,uint256)`. The call reverts, and the ERC-721 token remains permanently locked in the bridge.

`logMetadata` does call `IERC20Metadata(tokenAddress).decimals()`, which would revert for a standard ERC-721 (no `decimals()`), but `logMetadata` is **not** a prerequisite for `initTransfer`. The SECURITY.md explicitly confirms `logMetadata` is permissionless and independent: [4](#0-3) 

There is no on-chain enforcement that `logMetadata` must be called before `initTransfer`.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

An ERC-721 token deposited via `initTransfer` is irrecoverably locked in the `OmniBridge` contract. `finTransfer` will always revert when attempting to release it because ERC-721 has no `transfer(address,uint256)`. The only recovery path is a contract upgrade. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** Any unprivileged user can call `initTransfer` with an ERC-721 contract address. The only constraint is `fee < amount`, trivially satisfied with `fee = 0` and any positive `tokenId` cast to `uint128`. No prior registration, admin approval, or special role is required. Users who misidentify an NFT contract as a fungible token (e.g., ERC-721 tokens with ERC-20-like names/symbols) are a realistic scenario.

---

### Recommendation

1. **Add an ERC-165 interface check in `initTransfer`**: Before accepting a token, verify it does not implement `IERC721` (`0x80ac58cd`) using `IERC165.supportsInterface`. Revert if it does.
2. **Alternatively, enforce `logMetadata` as a prerequisite**: Require that `logMetadata` was successfully called for a token before it can be used in `initTransfer`. Since `logMetadata` calls `decimals()` which reverts on standard ERC-721, this would block ERC-721 tokens at the metadata stage.
3. **Minimum: add a `decimals()` call in `initTransfer`** for the fallback ERC-20 path, reverting if the call fails, mirroring the fix suggested in the referenced Linea report.

---

### Proof of Concept

1. Deploy a standard ERC-721 contract (e.g., OpenZeppelin `ERC721`). Mint token ID `1` to attacker.
2. Approve `OmniBridge` to transfer the NFT: `nft.approve(bridge, 1)`.
3. Call `bridge.initTransfer(nftAddress, 1, 0, 0, "victim.near", "")`.
   - `fee (0) < amount (1)` passes.
   - `customMinters[nftAddress] == address(0)` — passes.
   - `isBridgeToken[nftAddress] == false` — passes.
   - `IERC20(nftAddress).safeTransferFrom(attacker, bridge, 1)` → calls `nft.transferFrom(attacker, bridge, 1)` → **succeeds**. NFT is now in the bridge.
4. The NEAR-side relayer observes the `InitTransfer` event and processes the cross-chain message.
5. When `finTransfer` is called to release the token on EVM: `IERC20(nftAddress).safeTransfer(recipient, 1)` → calls `nft.transfer(recipient, 1)` → **reverts** (ERC-721 has no `transfer`).
6. The NFT is permanently locked. `completedTransfers[nonce]` is set to `true` at the start of `finTransfer`, so the nonce is consumed and the call cannot be retried. [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L382-413)
```text
        if (fee >= amount) {
            revert InvalidFee();
        }

        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
            if (customMinters[tokenAddress] != address(0)) {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    customMinters[tokenAddress],
                    amount
                );
                ICustomMinter(customMinters[tokenAddress]).burn(
                    tokenAddress,
                    amount
                );
            } else if (isBridgeToken[tokenAddress]) {
                BridgeToken(tokenAddress).burn(msg.sender, amount);
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
        }
```

**File:** evm/SECURITY.md (L8-8)
```markdown
- **`logMetadata` and `deployToken` are permissionless**: Anyone can call `logMetadata` for any ERC20, and anyone can submit a valid MPC signature to `deployToken`. This is by design — the bridge is fully permissionless
```
