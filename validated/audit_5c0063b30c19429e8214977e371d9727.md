### Title
Missing Zero-Address Recipient Check in `finTransfer` Enables Permanent ETH Loss — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` on EVM transfers native ETH to `payload.recipient` without validating that it is not `address(0)`. Because the recipient is user-specified at `initTransfer` time on the source chain and is embedded verbatim in the MPC-signed payload, a user who specifies the zero address as their EVM recipient will have their bridged ETH permanently burned with no recovery path.

---

### Finding Description

In `OmniBridge.finTransfer`, after signature verification, the contract dispatches funds to `payload.recipient` across several branches. The native-ETH branch (triggered when `payload.tokenAddress == address(0)`) is:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [1](#0-0) 

There is no guard of the form `require(payload.recipient != address(0))` anywhere before this call. A `.call{value: ...}("")` to `address(0)` succeeds on EVM (the call returns `success = true`), so the ETH is silently destroyed and the function emits a `FinTransfer` event marking the nonce as consumed. [2](#0-1) 

The `TransferMessagePayload.recipient` field is a plain `address` with no constraints: [3](#0-2) 

The codebase does expose an `OmniAddress::is_zero()` helper in the types library, but it is only used for Borsh encoding serialization, not for input validation in any transfer path: [4](#0-3) 

No zero-address guard was found in the NEAR `init_transfer` path either, meaning the zero address propagates all the way through to the EVM settlement.

---

### Impact Explanation

When `payload.tokenAddress == address(0)` (native ETH bridge) and `payload.recipient == address(0)`:

- The `.call{value: amount}("")` to `address(0)` **succeeds** on EVM.
- The destination nonce is marked consumed (`completedTransfers[nonce] = true`).
- The ETH is permanently destroyed; there is no refund mechanism and the nonce cannot be reused.
- The user's bridged funds are irrecoverably lost.

This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows.**

For ERC-20 and bridge-token branches the impact is a revert (OpenZeppelin's ERC-20 rejects `address(0)` recipients), so the critical path is exclusively the native-ETH branch.

---

### Likelihood Explanation

The scenario is realistic for automated relayer or wallet scripts that construct the `initTransfer` call on the source chain. If the EVM recipient field is left uninitialized or defaults to the zero value (analogous to the exploit scenario in the reference report), the MPC will sign the payload as-is, and `finTransfer` will execute without error. No privileged access is required; any bridge user can trigger this path.

---

### Recommendation

Add a zero-address guard at the top of `finTransfer`, before any fund movement:

```solidity
if (payload.recipient == address(0)) revert InvalidRecipient();
``` [5](#0-4) 

Additionally, consider adding a corresponding validation on the NEAR side inside `init_transfer` using the existing `OmniAddress::is_zero()` helper to reject zero-address recipients at origin, preventing the MPC from ever signing such a payload. [6](#0-5) 

---

### Proof of Concept

1. User calls `initTransfer` on NEAR (or any source chain) specifying the EVM recipient as `OmniAddress::Eth(Address::zero())` — i.e., `"eth:0x0000000000000000000000000000000000000000"` — with `tokenAddress = address(0)` (native ETH) and some `amount`.
2. The NEAR bridge emits an `InitTransfer` event; no zero-address check rejects it.
3. The MPC observes the event and signs a `TransferMessagePayload` with `recipient = address(0)`.
4. A relayer (or the user) calls `OmniBridge.finTransfer(signatureData, payload)` on EVM.
5. Signature verification passes (`ECDSA.recover(...) == nearBridgeDerivedAddress`). [7](#0-6) 
6. The `payload.tokenAddress == address(0)` branch executes: `address(0).call{value: amount}("")` returns `success = true`.
7. `completedTransfers[payload.destinationNonce] = true` — nonce is consumed, no retry possible. [2](#0-1) 
8. ETH is permanently burned; user has no recourse.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-314)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-322)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
```

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

**File:** near/omni-types/src/lib.rs (L292-296)
```rust
        if skip_zero_address && self.is_zero() {
            chain_str.to_string()
        } else {
            format!("{chain_str}{separator}{address}")
        }
```

**File:** near/omni-types/src/lib.rs (L299-313)
```rust
    pub fn is_zero(&self) -> bool {
        match self {
            Self::Eth(address)
            | Self::Arb(address)
            | Self::Base(address)
            | Self::Bnb(address)
            | Self::Pol(address)
            | Self::HyperEvm(address)
            | Self::Abs(address) => address.is_zero(),
            Self::Near(address) => *address == ZERO_ACCOUNT_ID,
            Self::Sol(address) | Self::Fogo(address) => address.is_zero(),
            Self::Btc(address) | Self::Zcash(address) => address.is_empty(),
            Self::Strk(address) => address.is_zero(),
        }
    }
```
