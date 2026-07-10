### Title
Fee-on-Transfer Token Accounting Mismatch in `initTransfer` Overcredits Cross-Chain Amount — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.initTransfer` records the caller-supplied `amount` in the cross-chain message without verifying how many tokens the contract actually received. For fee-on-transfer ERC-20 tokens the contract receives `amount − fee`, but the bridge emits and forwards `amount` to the destination chain, permanently overcrediting the recipient and undercollateralizing the vault.

---

### Finding Description

In `OmniBridge.initTransfer`, the non-bridge-token, non-custom-minter branch performs a `safeTransferFrom` and then immediately passes the caller-supplied `amount` to both `initTransferExtension` and the `InitTransfer` event:

```solidity
// OmniBridge.sol lines 406-412
} else {
    IERC20(tokenAddress).safeTransferFrom(
        msg.sender,
        address(this),
        amount          // ← user-controlled; actual receipt may be less
    );
}
``` [1](#0-0) 

Immediately after, the unverified `amount` is forwarded to the cross-chain layer:

```solidity
// OmniBridge.sol lines 415-436
initTransferExtension(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message, extensionValue
);
emit BridgeTypes.InitTransfer(
    msg.sender, tokenAddress, currentOriginNonce,
    amount, fee, nativeFee, recipient, message
);
``` [2](#0-1) 

In `OmniBridgeWormhole.initTransferExtension`, this same `amount` is serialized into the Wormhole payload that NEAR's prover will accept as authoritative:

```solidity
// OmniBridgeWormhole.sol lines 129-141
Borsh.encodeUint128(amount),   // ← overcredited amount
``` [3](#0-2) 

No balance snapshot is taken before or after the `safeTransferFrom`. There is no `balanceOf(address(this))` check to derive the real received amount.

On the NEAR side, `ft_on_transfer` receives the `amount` field from the proven message and records it verbatim as the transfer amount:

```rust
// near/omni-bridge/src/lib.rs line 543
amount,   // taken directly from the proven cross-chain message
``` [4](#0-3) 

The NEAR bridge then mints or releases `amount` tokens to the recipient on the destination chain, even though the EVM vault only holds `amount − transferFee`.

---

### Impact Explanation

**Impact: High** — Balance/accounting corruption that breaks bridge collateralization.

Each `initTransfer` call with a fee-on-transfer token inflates the cross-chain credit by exactly the transfer fee. Over time, the EVM vault becomes progressively undercollateralized. When users bridge tokens back from NEAR to EVM, `finTransfer` will attempt `safeTransfer(recipient, amount)` from the vault:

```solidity
// OmniBridge.sol lines 350-354
IERC20(payload.tokenAddress).safeTransfer(
    payload.recipient,
    payload.amount
);
``` [5](#0-4) 

Once the vault balance is exhausted, legitimate return transfers revert, permanently freezing user funds on NEAR. A deliberate attacker can accelerate this by repeatedly bridging fee-on-transfer tokens to drain the collateral gap.

---

### Likelihood Explanation

**Likelihood: Low-to-Medium.** Fee-on-transfer tokens exist on mainnet (e.g., STA, PAXG, tokens with configurable fees). The bridge does not whitelist tokens; any ERC-20 that is not in `isBridgeToken` or `customMinters` follows this code path. A user who simply bridges such a token triggers the bug without any special privilege. A motivated attacker can exploit it deliberately and repeatedly.

---

### Recommendation

Measure the actual received amount using a balance snapshot:

```solidity
} else {
    uint256 balanceBefore = IERC20(tokenAddress).balanceOf(address(this));
    IERC20(tokenAddress).safeTransferFrom(msg.sender, address(this), amount);
    uint256 balanceAfter = IERC20(tokenAddress).balanceOf(address(this));
    uint128 actualReceived = uint128(balanceAfter - balanceBefore);
    amount = actualReceived;   // use actual received amount downstream
}
```

Use `actualReceived` (not the caller-supplied `amount`) in `initTransferExtension` and the `InitTransfer` event. Alternatively, document that fee-on-transfer tokens are explicitly unsupported and add a token allowlist enforced on-chain.

---

### Proof of Concept

1. Deploy or use an existing fee-on-transfer ERC-20 token `FOT` (1% fee on every transfer) that is **not** in `isBridgeToken` and has no `customMinters` entry.
2. Approve `OmniBridge` for `1000` FOT.
3. Call `initTransfer(FOT, 1000, 0, 0, "near-recipient.near", "")`.
   - `safeTransferFrom` moves `1000` FOT from caller; contract receives `990` (1% fee taken).
   - `InitTransfer` event emits `amount = 1000`.
4. NEAR relayer observes the event, proves it, and calls `fin_transfer` on NEAR, crediting the recipient with `1000` omni-FOT.
5. The EVM vault is now short by `10` FOT per transfer.
6. Repeat N times. After enough iterations, the vault cannot cover legitimate return transfers; `finTransfer` on EVM reverts, permanently locking user funds on NEAR. [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L373-437)
```text
    function initTransfer(
        address tokenAddress,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
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

        initTransferExtension(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message,
            extensionValue
        );

        emit BridgeTypes.InitTransfer(
            msg.sender,
            tokenAddress,
            currentOriginNonce,
            amount,
            fee,
            nativeFee,
            recipient,
            message
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L129-141)
```text
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
```

**File:** near/omni-bridge/src/lib.rs (L540-553)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
```
