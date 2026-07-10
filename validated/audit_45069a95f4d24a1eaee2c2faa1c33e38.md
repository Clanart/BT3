### Title
Native Fee (STRK) Permanently Locked in StarkNet OmniBridge — No Withdrawal Mechanism - (File: starknet/src/omni_bridge.cairo)

---

### Summary

The StarkNet `OmniBridge` contract collects `native_fee` in STRK tokens during every `init_transfer` call, transferring them to the contract itself. The contract exposes no function to withdraw or distribute these accumulated tokens, causing all collected STRK native fees to be permanently locked and irrecoverable.

---

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function collects the caller-specified `native_fee` in STRK tokens and transfers them to the contract itself: [1](#0-0) 

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
```

The destination is `get_contract_address()` — the OmniBridge contract itself. The `IOmniBridge` interface exposes no `withdraw`, `rescue`, or admin token-transfer function: [2](#0-1) 

There is no mechanism anywhere in the contract to move the accumulated STRK tokens out. The `strk_token_address` is properly initialized in the constructor: [3](#0-2) 

so the token address is valid — the root cause is not an uninitialized address but an unrecoverable destination: the contract itself, with no egress path for those tokens.

On the NEAR side, the relayer's native-fee compensation for non-NEAR origin chains is handled by **minting** a wrapped native token on NEAR: [4](#0-3) 

This means the STRK locked in the StarkNet contract is never used to back or redeem anything — it simply accumulates and is permanently inaccessible.

---

### Impact Explanation

Every `init_transfer` call on StarkNet with `native_fee > 0` permanently locks STRK tokens in the OmniBridge contract. These tokens cannot be recovered by the protocol, distributed to relayers, or returned to users. The loss is cumulative and grows with bridge usage. This is a direct fee/accounting corruption that misdirects value — matching the **High** impact class: *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value."*

---

### Likelihood Explanation

**High.** The `native_fee` parameter is a standard, documented part of the bridge transfer flow. Any user who pays a non-zero `native_fee` when initiating a transfer from StarkNet triggers the lock. No special conditions, privileges, or adversarial behavior are required — this is a normal bridge operation.

---

### Recommendation

Add an admin-gated withdrawal function for the collected STRK native fees:

```cairo
fn withdraw_native_fees(
    ref self: ContractState,
    recipient: ContractAddress,
    amount: u256,
) {
    self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer(recipient, amount);
    assert(success, 'ERR_WITHDRAW_FAILED');
}
```

Alternatively, route the `native_fee` directly to a configurable fee-recipient address at collection time, analogous to how the NEAR bridge routes fees to the relayer.

---

### Proof of Concept

1. User calls `init_transfer` on the StarkNet OmniBridge with `native_fee = 1_000_000` (STRK units).
2. The contract executes:
   ```cairo
   IERC20Dispatcher { contract_address: strk_token }
       .transfer_from(caller, get_contract_address(), 1_000_000)
   ``` [1](#0-0) 
3. 1,000,000 STRK units are now held by the OmniBridge contract address.
4. Inspect the `IOmniBridge` interface — no `withdraw`, `rescue`, or fee-distribution function exists. [2](#0-1) 
5. The STRK tokens are permanently locked. Repeated bridge usage causes unbounded accumulation of irrecoverable protocol fees.

### Citations

**File:** starknet/src/omni_bridge.cairo (L9-32)
```text
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

**File:** starknet/src/omni_bridge.cairo (L134-134)
```text
        self.strk_token_address.write(strk_token_address);
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

**File:** near/omni-bridge/src/lib.rs (L2668-2673)
```rust
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```
