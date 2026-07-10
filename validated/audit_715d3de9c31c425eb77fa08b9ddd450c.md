### Title
Unbounded `message` Field in EVM and StarkNet `initTransfer` Causes Permanent Freeze of User Funds — (`evm/src/omni-bridge/contracts/OmniBridge.sol`, `starknet/src/omni_bridge.cairo`)

---

### Summary

The EVM `OmniBridge.initTransfer` and StarkNet `OmniBridge.init_transfer` accept a caller-supplied `message` string/`ByteArray` with no length restriction. Once tokens are locked or burned on the source chain, the relayer must submit a proof (or Wormhole VAA) containing the full message to NEAR's `fin_transfer`. If the message is large enough to push the NEAR transaction beyond NEAR's size limits, the proof can never be submitted, permanently freezing the user's funds with no on-chain recovery path.

---

### Finding Description

**NEAR side already enforces a bound.** When a transfer originates from NEAR, `InitTransferMsg::msg` is typed as `Option<BoundedString<MAX_INIT_TRANSFER_MSG_LEN>>` where `MAX_INIT_TRANSFER_MSG_LEN = 2048`. The `BoundedString` wrapper rejects oversized inputs at deserialization time on both JSON and Borsh paths. [1](#0-0) 

**EVM side has no equivalent guard.** `OmniBridge.initTransfer` accepts `string calldata message` with no length check. The function immediately locks/burns tokens, increments `currentOriginNonce`, and passes the raw string to `initTransferExtension` and the `InitTransfer` event. [2](#0-1) 

In the Wormhole variant, the full `message` string is Borsh-encoded directly into the Wormhole payload with no truncation or size check: [3](#0-2) 

**StarkNet side has no equivalent guard.** `OmniBridge.init_transfer` accepts `message: ByteArray` with no length assertion before burning/locking tokens and emitting the `InitTransfer` event: [4](#0-3) 

**NEAR `fin_transfer` stores the message as an unbounded `String`.** When the relayer submits the proof, `fin_transfer_callback` reconstructs a `TransferMessage` whose `msg` field is a plain `String` taken directly from the decoded `InitTransferMessage` — no size cap is applied at ingestion: [5](#0-4) 

The `InitTransferMessage` type used in proof results carries `msg: String` with no bound: [6](#0-5) 

**No strict sequence ordering blocks other transfers.** Unlike the OPinit system described in the reference report, the Omni Bridge uses per-transfer nonce bitmaps and proof-based finalization, so an oversized message does not block other users' transfers. The impact is scoped to the individual transfer whose proof cannot be submitted.

---

### Impact Explanation

Once `initTransfer` executes on EVM or StarkNet, the user's tokens are irrevocably locked or burned on the source chain. The only recovery path is a successful `fin_transfer` call on NEAR. If the proof payload (which must embed the full `message` content) exceeds NEAR's maximum transaction argument size, no relayer can ever submit it. There is no on-chain refund or cancellation mechanism on the source side. The user's funds are permanently frozen.

This matches the allowed impact class: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

- On EVM, `string calldata` is limited only by the block gas limit (~30M gas on Ethereum mainnet). A ~1 MB message costs roughly 16M gas — expensive but feasible in a single transaction. A user could accidentally pass raw binary data or a large JSON blob as the hook payload.
- On StarkNet, `ByteArray` is similarly unbounded at the contract level.
- The Solana `InitTransferPayload.message: String` is naturally bounded by Solana's ~1232-byte transaction size limit, making it a lower-risk vector.
- The NEAR side already applies `BoundedString<2048>` for NEAR-originated transfers, demonstrating developer awareness of the risk — but the fix was not applied symmetrically to the source-chain contracts. [7](#0-6) 

---

### Recommendation

1. **EVM**: Add a `require(bytes(message).length <= MAX_MESSAGE_LEN, "message too long")` guard at the top of `initTransfer` and `initTransfer1155`, where `MAX_MESSAGE_LEN` is set to match the NEAR-side bound (e.g., 2048 bytes).
2. **StarkNet**: Add `assert(message.len() <= MAX_MESSAGE_LEN, 'ERR_MESSAGE_TOO_LONG')` at the start of `init_transfer`.
3. **Solana**: Add a length check on `payload.message` in `InitTransfer::process` and `InitTransferSol::process`.
4. **NEAR ingestion path**: Optionally add a defensive truncation or rejection in `fin_transfer_callback` when `init_transfer.msg.len() > MAX_INIT_TRANSFER_MSG_LEN` to prevent any future source-chain bypass from causing a stuck transfer.

---

### Proof of Concept

**EVM → NEAR permanent freeze:**

```solidity
// Attacker calls initTransfer on OmniBridge (or OmniBridgeWormhole) with a ~1 MB message.
// Tokens are burned/locked immediately. currentOriginNonce is incremented.
string memory hugeMessage = new string(1_000_000); // 1 MB of zeros
omniBridge.initTransfer{value: nativeFee}(
    tokenAddress,
    amount,
    fee,
    nativeFee,
    "near:victim.near",
    hugeMessage          // no on-chain rejection
);
// The resulting Wormhole VAA or event proof is ~1 MB.
// NEAR's fin_transfer call with this proof exceeds NEAR's transaction size limit.
// The relayer cannot submit the proof. Tokens are permanently locked on EVM.
```

**StarkNet → NEAR permanent freeze:**

```cairo
// Attacker calls init_transfer with a large message ByteArray.
// Tokens are burned/locked. InitTransfer event is emitted with the full message.
let huge_message: ByteArray = "A" * 500_000; // 500 KB
dispatcher.init_transfer(
    token_address, amount, fee, native_fee,
    "near:victim.near",
    huge_message          // no length assertion
);
// NEAR relayer parses the event via parse_init_transfer (no size check on msg).
// fin_transfer proof submission to NEAR exceeds transaction size limits.
// Tokens are permanently locked on StarkNet.
``` [8](#0-7)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L128-150)
```text
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

**File:** starknet/src/omni_bridge.cairo (L281-331)
```text
        fn init_transfer(
            ref self: ContractState,
            token_address: ContractAddress,
            amount: u128,
            fee: u128,
            native_fee: u128,
            recipient: ByteArray,
            message: ByteArray,
        ) {
            assert(!_is_paused(@self, PAUSE_INIT_TRANSFER), 'ERR_INIT_TRANSFER_PAUSED');

            assert(amount > 0, 'ERR_ZERO_AMOUNT');
            assert(fee < amount, 'ERR_INVALID_FEE');

            let origin_nonce = self.current_origin_nonce.read() + 1;
            self.current_origin_nonce.write(origin_nonce);

            let caller = get_caller_address();

            if self.is_bridge_token(token_address) {
                IBridgeTokenDispatcher { contract_address: token_address }
                    .burn(caller, amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: token_address }
                    .transfer_from(caller, get_contract_address(), amount.into());
                assert(success, 'ERR_TRANSFER_FROM_FAILED');
            }

            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::InitTransfer(
                        InitTransfer {
                            sender: caller,
                            token_address,
                            origin_nonce,
                            amount,
                            fee,
                            native_fee,
                            recipient,
                            message,
                        },
                    ),
                )
        }
```

**File:** near/omni-bridge/src/lib.rs (L722-732)
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

**File:** solana/SECURITY.md (L17-18)
```markdown
- **No validation of `recipient` string in `InitTransferPayload`** — An invalid recipient causes the transfer to fail on the NEAR side after tokens are locked/burned on Solana. Manual intervention would be needed.
- **No validation of `fee_recipient` length in `FinalizeTransferPayload`** — Excessively large strings increase Wormhole message size. Bounded by Solana tx size limits in practice.
```

**File:** near/omni-types/src/starknet/events.rs (L53-75)
```rust
    let mut cursor = FeltCursor::new(data);
    let amount = cursor.read_u128()?;
    let fee = cursor.read_u128()?;
    let native_fee = cursor.read_u128()?;
    let recipient_str = cursor.read_byte_array()?;
    let msg = cursor.read_byte_array()?;

    let emitter_address = OmniAddress::Strk(H256(*from_address));
    let recipient: OmniAddress = recipient_str.parse().map_err(stringify)?;

    Ok(InitTransferMessage {
        origin_nonce,
        token,
        amount: near_sdk::json_types::U128(amount),
        recipient,
        fee: Fee {
            fee: near_sdk::json_types::U128(fee),
            native_fee: near_sdk::json_types::U128(native_fee),
        },
        sender,
        msg,
        emitter_address,
    })
```
