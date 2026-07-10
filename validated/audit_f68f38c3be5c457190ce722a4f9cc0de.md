### Title
STRK Native Fee Tokens Permanently Locked in StarkNet Bridge — No Withdrawal or Fee-Claim Path Exists - (File: `starknet/src/omni_bridge.cairo`)

---

### Summary

The StarkNet `OmniBridge` contract collects STRK tokens from users as `native_fee` during `init_transfer`, transferring them into the contract itself. However, the contract exposes no function to distribute, withdraw, or claim these accumulated STRK tokens. Every STRK `native_fee` payment is irrecoverably locked in the contract.

---

### Finding Description

In `init_transfer`, when `native_fee > 0`, the contract pulls STRK tokens from the caller directly into `get_contract_address()`:

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
``` [1](#0-0) 

The entire `IOmniBridge` interface exposes only these entry points:

```cairo
fn log_metadata / fn deploy_token / fn fin_transfer / fn init_transfer
fn upgrade_token / fn set_pause_flags / fn pause_all
fn get_token_address / fn is_bridge_token / fn is_transfer_finalised
``` [2](#0-1) 

None of these functions transfer STRK out of the contract. There is no `claim_fee`, `withdraw`, or admin sweep function anywhere in the contract module. The `fin_transfer` function only mints/transfers the bridged token to the recipient — it has no native-fee disbursement logic: [3](#0-2) 

The NEAR side does handle `native_fee` in `fin_transfer_send_tokens_callback` by **minting a wrapped STRK representation on NEAR** to the fee recipient:

```rust
if transfer_message.fee.native_fee.0 > 0 {
    let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());
    ext_token::ext(native_token_id)
        .with_static_gas(MINT_TOKEN_GAS)
        .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
        .detach();
}
``` [4](#0-3) 

This mints a NEAR-side token, not the actual STRK tokens held in the StarkNet contract. The real STRK tokens transferred into the StarkNet contract during `init_transfer` are never moved out.

---

### Impact Explanation

Every user who calls `init_transfer` on StarkNet with `native_fee > 0` permanently loses their STRK tokens to the contract with no recovery path. The STRK balance of the StarkNet bridge contract grows monotonically and is irrecoverable. This matches the allowed Critical impact: **permanent freezing / irrecoverable lock of user funds in a bridge flow**.

---

### Likelihood Explanation

`native_fee` is a documented, first-class parameter of `init_transfer` — it is parsed from StarkNet events by the NEAR prover: [5](#0-4) 

Any unprivileged bridge user who pays a `native_fee` to incentivize a relayer triggers the lock. No special role or condition is required. The call path is fully public and reachable.

---

### Recommendation

Add a privileged `claim_native_fee` (or `withdraw_strk`) function to the StarkNet contract that allows an authorized role (e.g., `DEFAULT_ADMIN_ROLE` or a dedicated fee-recipient role) to transfer accumulated STRK tokens out of the contract to a designated fee recipient. Alternatively, mirror the EVM/Solana pattern and transfer the `native_fee` directly to a fee-recipient address at `init_transfer` time rather than holding it in the contract.

---

### Proof of Concept

1. Alice calls `init_transfer(token_address, 1000, 10, 50, "alice.near", "")` on the StarkNet `OmniBridge`.
2. The contract executes `transfer_from(Alice, OmniBridge, 50)` for STRK (the `native_fee`).
3. The `InitTransfer` event is emitted; NEAR processes it and mints 50 wrapped-STRK to the relayer on NEAR.
4. The 50 real STRK tokens now sit in the StarkNet `OmniBridge` contract.
5. No function in `IOmniBridge` can move them. They are permanently locked.
6. Repeat for every user who pays a `native_fee` — the locked STRK balance grows without bound and is unrecoverable.

### Citations

**File:** starknet/src/omni_bridge.cairo (L8-32)
```text
#[starknet::interface]
pub trait IOmniBridge<TContractState> {
    fn log_metadata(ref self: TContractState, token: ContractAddress);
    fn deploy_token(ref self: TContractState, signature: Signature, payload: MetadataPayload);
    fn fin_transfer(
        ref self: TContractState, signature: Signature, payload: TransferMessagePayload,
    );
    fn init_transfer(
        ref self: TContractState,
        token_address: ContractAddress,
        amount: u128,
        fee: u128,
        native_fee: u128,
        recipient: ByteArray,
        message: ByteArray,
    );
    fn upgrade_token(
        ref self: TContractState, token_address: ContractAddress, new_class_hash: ClassHash,
    );
    fn set_pause_flags(ref self: TContractState, flags: u8);
    fn pause_all(ref self: TContractState);
    fn get_token_address(self: @TContractState, token_id: ByteArray) -> ContractAddress;
    fn is_bridge_token(self: @TContractState, token_address: ContractAddress) -> bool;
    fn is_transfer_finalised(self: @TContractState, nonce: u64) -> bool;
}
```

**File:** starknet/src/omni_bridge.cairo (L242-279)
```text
        fn fin_transfer(
            ref self: ContractState, signature: Signature, payload: TransferMessagePayload,
        ) {
            assert(!_is_paused(@self, PAUSE_FIN_TRANSFER), 'ERR_FIN_TRANSFER_PAUSED');

            assert(
                !self.is_transfer_finalised(payload.destination_nonce), 'ERR_NONCE_ALREADY_USED',
            );
            _set_transfer_finalised(ref self, payload.destination_nonce);

            _verify_borsh_signature(
                ref self, @payload.to_borsh(self.omni_bridge_chain_id.read()), signature,
            );

            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }

            self
                .emit(
                    Event::FinTransfer(
                        FinTransfer {
                            origin_chain: payload.origin_chain,
                            origin_nonce: payload.origin_nonce,
                            token_address: payload.token_address,
                            amount: payload.amount,
                            recipient: payload.recipient,
                            fee_recipient: payload.fee_recipient,
                            message: payload.message,
                        },
                    ),
                )
        }
```

**File:** starknet/src/omni_bridge.cairo (L309-314)
```text
            if native_fee > 0 {
                let native_token = self.strk_token_address.read();
                let success = IERC20Dispatcher { contract_address: native_token }
                    .transfer_from(caller, get_contract_address(), native_fee.into());
                assert(success, 'ERR_FEE_TRANSFER_FAILED');
            }
```

**File:** near/omni-bridge/src/lib.rs (L1736-1743)
```rust
            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```

**File:** near/omni-types/src/starknet/events.rs (L54-71)
```rust
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
```
