### Title
Collected `native_fee` (STRK) Permanently Locked in StarkNet Bridge — No Release Entry Point Exists - (File: `starknet/src/omni_bridge.cairo`)

### Summary

The StarkNet `OmniBridge` contract collects `native_fee` in STRK tokens from users during `init_transfer`, but no function exists anywhere in the contract to release or distribute those STRK tokens to a fee recipient. Every `init_transfer` call with `native_fee > 0` permanently locks STRK in the bridge contract with no recovery path.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a `native_fee: u128` parameter. When non-zero, it pulls STRK tokens from the caller into the bridge contract itself:

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
``` [1](#0-0) 

The full `IOmniBridge` interface exposed by the contract is:

```cairo
pub trait IOmniBridge<TContractState> {
    fn log_metadata(...);
    fn deploy_token(...);
    fn fin_transfer(...);
    fn init_transfer(...);
    fn upgrade_token(...);
    fn set_pause_flags(...);
    fn pause_all(...);
    fn get_token_address(...) -> ContractAddress;
    fn is_bridge_token(...) -> bool;
    fn is_transfer_finalised(...) -> bool;
}
``` [2](#0-1) 

None of these functions transfer STRK out of the contract to a fee recipient. There is no `claim_fee`, `withdraw_native_fee`, `dump_fees`, or any equivalent entry point. The `UpgradeableImpl` only allows upgrading the contract class hash — it does not release tokens. [3](#0-2) 

The `fin_transfer` function on the destination chain only transfers `payload.amount` to `payload.recipient` and emits `fee_recipient` in an event — it never pays out any fee: [4](#0-3) 

### Impact Explanation

Every user who calls `init_transfer` on StarkNet with `native_fee > 0` permanently locks STRK ERC-20 tokens inside the bridge contract. There is no admin withdrawal, no fee-claim function, and no upgrade path that could release these tokens without deploying an entirely new contract class. This constitutes an irrecoverable lock of user-paid protocol funds in the bridge vault flow.

**Impact class:** Critical — Permanent freezing / irrecoverable lock of user or protocol funds in bridge flows.

### Likelihood Explanation

The `native_fee` parameter is a standard, documented part of the `init_transfer` interface. Any user who wishes to incentivize a relayer by paying a native STRK fee will trigger this lock. The call path is fully permissionless and requires no special role. Likelihood is **High**.

### Recommendation

Add a privileged `claim_native_fees(recipient: ContractAddress)` (or equivalent) function to the `IOmniBridge` interface and its implementation that transfers the accumulated STRK balance held by the contract to a designated fee recipient. This mirrors the `DumpFees` fix applied in the referenced OffchainBook patch: add the missing entry point so that collected fees can actually be disbursed.

### Proof of Concept

1. Alice calls `init_transfer(token_address, amount=1000, fee=10, native_fee=50, recipient="alice.near", message="")` on the StarkNet `OmniBridge`.
2. The contract executes `IERC20Dispatcher { contract_address: strk_token }.transfer_from(Alice, bridge_contract, 50)` — 50 STRK are now held by the bridge.
3. The relayer observes the `InitTransfer` event and calls `sign_transfer` on NEAR, receiving their fee in NEAR tokens.
4. The 50 STRK remain in the StarkNet bridge contract indefinitely. No function in `IOmniBridge` can move them. They are permanently locked.
5. Repeat for every user who pays `native_fee > 0`: STRK accumulates in the contract with zero recovery path. [5](#0-4)

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

**File:** starknet/src/omni_bridge.cairo (L256-279)
```text
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

**File:** starknet/src/omni_bridge.cairo (L389-395)
```text
    #[abi(embed_v0)]
    impl UpgradeableImpl of IUpgradeable<ContractState> {
        fn upgrade(ref self: ContractState, new_class_hash: ClassHash) {
            self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
            self.upgradeable.upgrade(new_class_hash);
        }
    }
```
