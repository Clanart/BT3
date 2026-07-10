### Title
Native Fee (STRK) Permanently Locked in StarkNet Bridge Contract With No Withdrawal Mechanism - (`starknet/src/omni_bridge.cairo`)

---

### Summary

The StarkNet `OmniBridge` contract collects `native_fee` (STRK tokens) from users during `init_transfer` by transferring them into the bridge contract itself. However, the contract exposes no function to withdraw or redistribute these accumulated STRK tokens. Every STRK paid as a native fee is permanently locked in the contract.

---

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts an optional `native_fee` parameter. When non-zero, it pulls STRK tokens from the caller directly into the bridge contract address: [1](#0-0) 

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
```

The full public interface of `IOmniBridge` is: [2](#0-1) 

None of the exposed functions — `log_metadata`, `deploy_token`, `fin_transfer`, `init_transfer`, `upgrade_token`, `set_pause_flags`, `pause_all`, `get_token_address`, `is_bridge_token`, `is_transfer_finalised` — transfer STRK tokens out of the contract. There is no `withdraw_fees`, `rescue`, or equivalent function.

The StarkNet CLAUDE.md confirms the design intent: *"Fees are deducted on NEAR side before signing"* and *"Optional native token fees in `init_transfer` (e.g., for gas)"*. This means the fee distribution to relayers is handled entirely on NEAR (via `fin_transfer_send_tokens_callback` minting a wrapped STRK token to the fee recipient), while the actual STRK tokens collected on StarkNet are never disbursed. [3](#0-2) 

The NEAR side mints a *wrapped* native token representation to the fee recipient — it does not touch the STRK tokens sitting in the StarkNet contract. Those tokens have no exit path.

The contract storage has no balance-tracking field for accumulated fees: [4](#0-3) 

---

### Impact Explanation

Every user who calls `init_transfer` on StarkNet with `native_fee > 0` permanently loses those STRK tokens to the bridge contract. The tokens accumulate with no mechanism for the user, relayer, or admin to recover them. This is a **permanent, irrecoverable lock of user funds** in the bridge contract, matching the critical impact class: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

High. The `native_fee` parameter is a standard, documented part of the StarkNet bridge API used to incentivize relayers. Any user following the normal bridge flow who sets `native_fee > 0` triggers the loss. No special conditions, privileged access, or race conditions are required — the loss occurs on every such call.

---

### Recommendation

Add an admin-gated withdrawal function to the StarkNet contract that transfers accumulated STRK fees to a designated recipient (e.g., a fee treasury or relayer distributor):

```cairo
fn withdraw_native_fees(ref self: ContractState, recipient: ContractAddress, amount: u128) {
    self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer(recipient, amount.into());
    assert(success, 'ERR_FEE_WITHDRAWAL_FAILED');
}
```

Alternatively, redesign `init_transfer` to forward the `native_fee` directly to a pre-configured fee recipient rather than holding it in the contract.

---

### Proof of Concept

1. User calls `init_transfer` on the StarkNet `OmniBridge` with `native_fee = 1_000_000_000_000_000_000` (1 STRK).
2. The contract executes `transfer_from(caller, get_contract_address(), 1e18)` — STRK moves into the bridge contract. [5](#0-4) 
3. The `InitTransfer` event is emitted. The NEAR relayer picks it up and calls `fin_transfer_send_tokens_callback` on NEAR, which mints a wrapped STRK token to the fee recipient — but this does not touch the actual STRK in the StarkNet contract. [6](#0-5) 
4. Inspecting the full `IOmniBridge` interface confirms there is no function to withdraw the STRK from the StarkNet contract. [2](#0-1) 
5. The 1 STRK is permanently locked. Repeating across all users compounds the loss indefinitely.

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

**File:** starknet/src/omni_bridge.cairo (L101-120)
```text
    #[storage]
    struct Storage {
        #[substorage(v0)]
        accesscontrol: AccessControlComponent::Storage,
        #[substorage(v0)]
        src5: SRC5Component::Storage,
        #[substorage(v0)]
        upgradeable: UpgradeableComponent::Storage,
        pause_flags: u8,
        bridge_token_class_hash: ClassHash,
        current_origin_nonce: u64,
        // Bitmap: slot = nonce / 251, bit = nonce % 251
        completed_transfers: Map<u64, felt252>,
        starknet_to_near_token: Map<ContractAddress, ByteArray>,
        // Can't use ByteArray as a key. Using hash instead
        near_to_starknet_token: Map<u256, ContractAddress>,
        omni_bridge_chain_id: u8,
        omni_bridge_derived_address: EthAddress,
        strk_token_address: ContractAddress,
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
