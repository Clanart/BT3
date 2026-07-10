### Title
Native Fee (STRK) Permanently Locked in Starknet Bridge — No Withdrawal Mechanism - (File: starknet/src/omni_bridge.cairo)

### Summary

The Starknet `OmniBridge` contract collects `native_fee` in STRK tokens from users during `init_transfer`, transferring them into the contract itself. However, unlike the NEAR hub (which has `claim_fee`) and the EVM bridge (which distributes fees via `finTransfer`), the Starknet contract has no function to withdraw or distribute these accumulated STRK tokens. Every STRK token paid as `native_fee` is permanently locked.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a `native_fee` parameter denominated in STRK. When `native_fee > 0`, it pulls STRK from the caller into the contract:

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
``` [1](#0-0) 

The full `IOmniBridge` interface exposes only: `log_metadata`, `deploy_token`, `fin_transfer`, `init_transfer`, `upgrade_token`, `set_pause_flags`, `pause_all`, `get_token_address`, `is_bridge_token`, `is_transfer_finalised`. [2](#0-1) 

None of these functions transfer STRK out of the contract. The `fin_transfer` function mints or transfers the bridged token to the recipient but performs no fee distribution. [3](#0-2) 

By contrast, the NEAR bridge has a dedicated `claim_fee` entry point that verifies a proof and sends the fee to the relayer. [4](#0-3) 

### Impact Explanation

Every STRK token paid as `native_fee` on Starknet is irrecoverably locked in the bridge contract. There is no admin rescue path, no relayer claim path, and no user refund path. This matches the allowed impact: **Critical — Permanent freezing / irrecoverable lock of user or protocol funds in bridge flows.**

### Likelihood Explanation

The `native_fee` field is a standard parameter of `init_transfer` and is explicitly documented as the mechanism for users to pay relayers on non-NEAR chains. Any user who sets `native_fee > 0` when bridging from Starknet triggers the lock. This is a normal, expected usage path, not an edge case.

### Recommendation

Add a `claim_fee` (or equivalent) function to the Starknet bridge that allows an authorized relayer (verified via proof or signature, mirroring the NEAR `claim_fee_callback` pattern) to withdraw accumulated STRK fees. Alternatively, forward the `native_fee` directly to a designated fee recipient address at the time of `init_transfer` rather than holding it in the contract.

### Proof of Concept

1. User calls `init_transfer` on the Starknet bridge with `native_fee = 1_000_000_000_000_000_000` (1 STRK).
2. The contract executes `transfer_from(caller, get_contract_address(), 1e18)` — STRK enters the contract. [1](#0-0) 
3. The relayer submits the corresponding proof on NEAR and calls `fin_transfer` on Starknet to release the bridged token to the recipient. [3](#0-2) 
4. No function in the Starknet contract moves the 1 STRK out. The STRK balance of the contract increases by 1 STRK permanently.
5. Repeating across all users who pay `native_fee > 0` accumulates an unbounded, irrecoverable STRK balance in the contract.

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

**File:** near/omni-bridge/src/lib.rs (L1057-1064)
```rust
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }
```
