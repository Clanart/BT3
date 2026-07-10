### Title
Native Fee STRK Tokens Permanently Locked in StarkNet Bridge Contract — (`starknet/src/omni_bridge.cairo`)

### Summary

The StarkNet `OmniBridge` contract collects STRK tokens as `native_fee` during `init_transfer`, but provides no mechanism to ever release those tokens. They accumulate in the contract permanently with no withdrawal path for relayers, users, or admins.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a `native_fee` parameter. When non-zero, it pulls STRK tokens from the caller into the contract: [1](#0-0) 

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
```

The `fin_transfer` function — the only other state-changing function in the contract — only transfers the bridged token amount to the recipient. It does not pay out any native fee to a relayer or fee recipient: [2](#0-1) 

The full public interface of the contract exposes no `claim_native_fee`, `withdraw_native_fee`, or equivalent function: [3](#0-2) 

The NEAR-side bridge handles native fees by paying them out in `send_fee_internal` to the relayer (either as yoctoNEAR or by minting a wrapped native token). But this payout is sourced from the NEAR contract's own balance or minting — it does not correspond to releasing the STRK tokens locked on StarkNet: [4](#0-3) 

The STRK tokens deposited into the StarkNet contract as `native_fee` have no corresponding release path anywhere in the codebase.

### Impact Explanation

Every `init_transfer` call on StarkNet that includes a non-zero `native_fee` permanently locks STRK tokens in the contract. There is no admin rescue function, no `claim_fee` path, and no upgrade-triggered drain. The funds are irrecoverable. This matches the **Critical** impact class: permanent freezing of user funds in a bridge flow.

### Likelihood Explanation

The `native_fee` parameter is a documented, user-facing field present in the `InitTransfer` event: [5](#0-4) 

Any user who pays a non-zero `native_fee` to incentivize a relayer loses those STRK tokens permanently. This is triggered by normal, unprivileged user interaction with no special preconditions.

### Recommendation

Add a `claim_native_fee` function (or extend `fin_transfer`) that pays out the accumulated STRK native fee to the designated `fee_recipient` specified in the `TransferMessagePayload`. The payout should be gated on the same nonce/signature verification already used in `fin_transfer`, so only a legitimately finalized transfer can trigger the fee release.

### Proof of Concept

1. User calls `init_transfer` on StarkNet with `native_fee = 1_000_000_000_000_000_000` (1 STRK).
2. The contract executes `transfer_from(caller, get_contract_address(), 1e18)` — STRK is now held by the bridge.
3. Relayer submits proof on NEAR and calls `fin_transfer` / `claim_fee` on NEAR, receiving compensation sourced from NEAR-side minting or NEAR balance.
4. The 1 STRK sitting in the StarkNet contract is never touched again. No function in the contract can move it.
5. After N such transfers, N STRK are permanently locked. The contract's STRK balance grows monotonically with no drain path. [1](#0-0) [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L2664-2673)
```rust
            } else if origin_chain == ChainKind::Near {
                Promise::new(fee_recipient.clone())
                    .transfer(NearToken::from_yoctonear(transfer_message.fee.native_fee.0))
                    .detach();
            } else {
                ext_token::ext(self.get_native_token_id(origin_chain))
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }
```

**File:** starknet/src/bridge_types.cairo (L107-120)
```text
#[derive(Drop, starknet::Event)]
pub struct InitTransfer {
    #[key]
    pub sender: ContractAddress,
    #[key]
    pub token_address: ContractAddress,
    #[key]
    pub origin_nonce: u64,
    pub amount: u128,
    pub fee: u128,
    pub native_fee: u128,
    pub recipient: ByteArray,
    pub message: ByteArray,
}
```
