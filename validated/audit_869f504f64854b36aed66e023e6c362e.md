### Title
Unbounded `msg` Field in `InitTransferMessage` Enables Permanent Fund Locking via Gas Exhaustion in `fin_transfer_callback` — (`near/omni-types/src/prover_result.rs`, `near/omni-bridge/src/lib.rs`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The EVM `initTransfer` function accepts an unbounded `string calldata message` parameter with no length validation. This value is published into a Wormhole VAA, parsed by the prover into `InitTransferMessage.msg: String` (also unbounded), and then processed inside `fin_transfer_callback` on NEAR — which operates under a fixed static gas allocation. A sufficiently large `message` causes the callback to exhaust its gas budget and panic, permanently preventing the transfer from being finalized on NEAR while the user's tokens remain irrecoverably locked on EVM.

---

### Finding Description

**Root cause — EVM side (no length limit):**

`OmniBridge.sol::initTransfer` accepts `string calldata message` with no length check:

```solidity
function initTransfer(
    address tokenAddress, uint128 amount, uint128 fee, uint128 nativeFee,
    string calldata recipient,
    string calldata message          // ← no length limit
) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
```

This string is Borsh-encoded and published verbatim into a Wormhole message via `OmniBridgeWormhole.sol::initTransferExtension`:

```solidity
Borsh.encodeString(message)
```

**Root cause — prover result type (unbounded):**

The prover decodes the VAA into `InitTransferMessage`, where `msg` is a plain `String` with no bound:

```rust
pub struct InitTransferMessage {
    pub msg: String,   // ← unbounded; no BoundedString wrapper
    ...
}
```

This contrasts directly with the NEAR-originated path, where `InitTransferMsg.msg` is explicitly capped:

```rust
pub msg: Option<BoundedString<MAX_INIT_TRANSFER_MSG_LEN>>,  // 2048 bytes
```

**Root cause — NEAR processing (fixed gas, variable work):**

`fin_transfer_callback` is invoked with a fixed static gas budget (`FIN_TRANSFER_CALLBACK_GAS`). It copies the unbounded `msg` into a `TransferMessage`, then either:

- Calls `process_fin_transfer_to_near`, which clones the large string and passes it as the memo to `ft_transfer_call` (gas cost proportional to string size), or
- Calls `process_fin_transfer_to_other_chain`, which stores the full `TransferMessage` (including the large `msg`) in `pending_transfers` (storage cost proportional to string size, with no deposit check sized for the actual message).

```rust
let transfer_message = TransferMessage {
    msg: init_transfer.msg,   // ← large string copied unconditionally
    ...
};
```

If the callback panics (gas exhaustion), NEAR rolls back all state changes. The transfer nonce is not consumed, so the relayer can retry — but every retry will fail identically because the VAA payload is immutable.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

Once `initTransfer` is called on EVM, the tokens are locked in (or burned by) the bridge contract. There is no cancel or refund function on the EVM side. If `fin_transfer_callback` on NEAR always panics due to gas exhaustion from the oversized `msg`, the transfer can never be finalized. The user's tokens are permanently locked on EVM with no recovery path short of a contract upgrade and manual admin intervention.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The attack is reachable by any unprivileged EVM user who calls `initTransfer` directly. No special role, leaked key, or colluding MPC signer is required. The attacker pays EVM gas to lock their own tokens (or, more dangerously, a legitimate user accidentally sends a large hook payload). The Wormhole core bridge imposes a practical upper bound on VAA payload size (typically ~10 KB), but even a few kilobytes of `msg` data, when deserialized, cloned, and forwarded as `ft_transfer_call` memo inside a fixed-gas NEAR callback, can exhaust the budget. For the MPC prover path there is no Wormhole size cap at all.

---

### Recommendation

1. **Add a length cap on `InitTransferMessage.msg`** in `near/omni-types/src/prover_result.rs`, mirroring the `BoundedString<MAX_INIT_TRANSFER_MSG_LEN>` already used for NEAR-originated transfers.
2. **Validate `msg` length in `fin_transfer_callback`** before constructing `TransferMessage`, rejecting oversized payloads with a clear error rather than panicking mid-callback.
3. **Add a length limit on `message` in EVM `initTransfer`** (e.g., `require(bytes(message).length <= MAX_MSG_LEN)`), preventing oversized payloads from ever entering the pipeline.
4. **Apply the same cap to `FastFinTransferMsg.msg` and `UtxoFinTransferMsg.msg`**, which are also plain unbounded `String` fields.

---

### Proof of Concept

**Step 1 — Attacker calls `initTransfer` on EVM with a large `message`:**

```solidity
// message is ~10 KB of arbitrary bytes — no length check in OmniBridge.sol
omniBridge.initTransfer{value: nativeFee}(
    tokenAddress, amount, fee, nativeFee,
    "near:victim.near",
    largeMessage   // string of length >> MAX_INIT_TRANSFER_MSG_LEN
);
``` [1](#0-0) 

**Step 2 — Wormhole VAA is published with the large payload via `initTransferExtension`:** [2](#0-1) 

**Step 3 — Prover decodes VAA into `InitTransferMessage` with unbounded `msg: String`:** [3](#0-2) 

**Step 4 — `fin_transfer_callback` copies the large string into `TransferMessage` and calls `process_fin_transfer_to_near` under a fixed gas budget:** [4](#0-3) 

**Step 5 — Gas exhaustion causes callback to panic; state rolls back; tokens remain locked on EVM permanently.**

**Contrast with the bounded NEAR-originated path:** [5](#0-4) 

The asymmetry is clear: NEAR-originated `msg` is capped at 2048 bytes via `BoundedString`, but EVM-originated `msg` arriving through `InitTransferMessage` has no cap at all.

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
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
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** near/omni-types/src/prover_result.rs (L9-18)
```rust
pub struct InitTransferMessage {
    pub origin_nonce: Nonce,
    pub token: OmniAddress,
    pub amount: U128,
    pub recipient: OmniAddress,
    pub fee: Fee,
    pub sender: OmniAddress,
    pub msg: String,
    pub emitter_address: OmniAddress,
}
```

**File:** near/omni-bridge/src/lib.rs (L722-745)
```rust
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```

**File:** near/omni-types/src/lib.rs (L477-496)
```rust
/// Maximum byte length for `InitTransferMsg::msg` — caps the user-supplied hook payload
/// forwarded to the destination chain.
pub const MAX_INIT_TRANSFER_MSG_LEN: usize = 2048;
/// Maximum byte length for `InitTransferMsg::external_id` — large enough for UUIDs or
/// hex-encoded 32-byte hashes, small enough to bound storage-account-hash inputs.
pub const MAX_EXTERNAL_ID_LEN: usize = 64;

#[derive(Serialize, Deserialize, Debug, Clone)]
pub struct InitTransferMsg {
    pub recipient: OmniAddress,
    pub fee: U128,
    pub native_token_fee: U128,
    /// Optional caller-supplied destination-chain hook payload. Length-capped to
    /// [`MAX_INIT_TRANSFER_MSG_LEN`] bytes to prevent unbounded storage/gas inflation.
    pub msg: Option<BoundedString<MAX_INIT_TRANSFER_MSG_LEN>>,
    /// Optional caller-provided identifier mixed into the virtual storage account ID hash.
    /// Lets otherwise-identical transfers derive distinct storage accounts so their
    /// storage deposits do not collide. Length-capped to [`MAX_EXTERNAL_ID_LEN`] bytes.
    pub external_id: Option<BoundedString<MAX_EXTERNAL_ID_LEN>>,
}
```
