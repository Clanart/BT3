### Title
Missing Zero-Address Validation for Transfer Recipient Causes Permanent Fund Lock — (`near/omni-bridge/src/lib.rs` + `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

A user can initiate a cross-chain transfer on NEAR with `recipient = OmniAddress::Eth(H160::ZERO)` (the EVM zero address). NEAR's `init_transfer` performs no zero-address check on the recipient. The MPC then signs the transfer message. When `finTransfer` is called on EVM, the ERC-20 `safeTransfer` (or `mint`) to `address(0)` reverts — as OpenZeppelin ERC-20 forbids transfers to the zero address — causing the entire transaction to revert. Because the nonce is set before the transfer but reverts with it, the nonce is never consumed, so `finTransfer` can never succeed. The tokens burned/locked on NEAR are permanently irrecoverable.

---

### Finding Description

**Root cause — NEAR side (`init_transfer`):**

`init_transfer` in `near/omni-bridge/src/lib.rs` validates only that the recipient chain is not NEAR:

```rust
require!(
    init_transfer_msg.recipient.get_chain() != ChainKind::Near,
    BridgeError::InvalidRecipientChain.as_ref()
);
```

There is no check that `!init_transfer_msg.recipient.is_zero()`. The `OmniAddress` type already exposes `is_zero()` (defined in `near/omni-types/src/lib.rs`) and `OmniAddress::new_zero()` is a valid constructor, confirming the zero address is a representable value. A user can freely pass `OmniAddress::Eth(H160::ZERO)` as the recipient. [1](#0-0) [2](#0-1) [3](#0-2) 

**Root cause — EVM side (`finTransfer`):**

`finTransfer` in `OmniBridge.sol` marks the nonce used and then attempts the token transfer to `payload.recipient`:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287
// ...
IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount);  // line 351
// or
IBridgeToken(payload.tokenAddress).mint(payload.recipient, payload.amount);    // line 339
```

OpenZeppelin's `ERC20._transfer` and `ERC20._mint` both revert when `to == address(0)`. If `payload.recipient` is `address(0)`, the token transfer reverts, rolling back the entire transaction — including the `completedTransfers` assignment. The nonce is therefore never consumed, and every subsequent call to `finTransfer` for this transfer will also revert. There is no zero-address guard before the transfer. [4](#0-3) [5](#0-4) 

---

### Impact Explanation

The tokens are burned or locked on NEAR during `init_transfer`. No refund or cancellation path exists in the NEAR bridge for a transfer whose `finTransfer` permanently fails. The `finTransfer` on EVM will revert on every attempt (nonce never consumed, but transfer always fails), making the funds irrecoverable. This is a **permanent, irrecoverable lock of user funds** in the bridge flow.

Impact: **Critical** — matches "Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."

---

### Likelihood Explanation

Any unprivileged user who calls `init_transfer` on NEAR controls the `recipient` field directly. No special role, leaked key, or colluding party is required. A user may do this accidentally (e.g., passing a default/uninitialized address) or deliberately (self-grief). The MPC will sign the message because NEAR emits it as a valid transfer event. The EVM `finTransfer` caller (relayer) has no ability to override the signed recipient.

---

### Recommendation

**NEAR side** — add a zero-address guard in `init_transfer` before recording the transfer:

```rust
require!(
    !init_transfer_msg.recipient.is_zero(),
    BridgeError::InvalidRecipient.as_ref()
);
```

**EVM side** — add a guard in `finTransfer` before any token transfer:

```solidity
require(payload.recipient != address(0), "Invalid recipient: zero address");
```

The NEAR-side check is the primary fix (prevents the invalid state from entering the system). The EVM-side check is defense-in-depth.

---

### Proof of Concept

1. User calls `init_transfer` on NEAR with:
   - `token_id`: any bridged ERC-20 (e.g., USDC)
   - `amount`: 1000 USDC
   - `recipient`: `OmniAddress::Eth(H160::ZERO)` — the EVM zero address
   - No zero-address check fires; NEAR burns/locks 1000 USDC and emits the transfer event.

2. MPC observes the event and signs the `TransferMessagePayload` with `recipient = address(0)`.

3. Relayer calls `finTransfer(signatureData, payload)` on EVM:
   - Line 287: `completedTransfers[destinationNonce] = true` — set.
   - Line 311: Signature verified — passes.
   - Line 351: `IERC20(usdc).safeTransfer(address(0), 1000e6)` — **reverts** (OZ ERC-20 forbids `to == address(0)`).
   - Entire transaction reverts; `completedTransfers` assignment is rolled back.

4. Every subsequent `finTransfer` call for this nonce reverts identically. The 1000 USDC burned on NEAR is permanently lost with no reclaim path. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L523-557)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

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
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-types/src/lib.rs (L174-175)
```rust
pub const ZERO_ACCOUNT_ID: &str =
    "0000000000000000000000000000000000000000000000000000000000000000";
```

**File:** near/omni-types/src/lib.rs (L197-213)
```rust
    pub fn new_zero(chain_kind: ChainKind) -> Result<Self, String> {
        match chain_kind {
            ChainKind::Eth => Ok(Self::Eth(H160::ZERO)),
            ChainKind::Near => Ok(Self::Near(ZERO_ACCOUNT_ID.parse().map_err(stringify)?)),
            ChainKind::Sol => Ok(Self::Sol(SolAddress::ZERO)),
            ChainKind::Arb => Ok(Self::Arb(H160::ZERO)),
            ChainKind::Base => Ok(Self::Base(H160::ZERO)),
            ChainKind::Bnb => Ok(Self::Bnb(H160::ZERO)),
            ChainKind::Pol => Ok(Self::Pol(H160::ZERO)),
            ChainKind::HyperEvm => Ok(Self::HyperEvm(H160::ZERO)),
            ChainKind::Btc => Ok(Self::Btc(String::new())),
            ChainKind::Zcash => Ok(Self::Zcash(String::new())),
            ChainKind::Strk => Ok(Self::Strk(H256::ZERO)),
            ChainKind::Abs => Ok(Self::Abs(H160::ZERO)),
            ChainKind::Fogo => Ok(Self::Fogo(SolAddress::ZERO)),
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-355)
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

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
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
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```
