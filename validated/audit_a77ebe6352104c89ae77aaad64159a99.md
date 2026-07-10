### Title
Accumulated STRK Native Fees Are Permanently Locked With No Withdrawal Path - (`starknet/src/omni_bridge.cairo`)

### Summary
The StarkNet `OmniBridge` contract collects `native_fee` in STRK tokens from every `init_transfer` call, transferring them into the contract itself. However, the contract exposes no function to withdraw or distribute these accumulated STRK tokens. Every bridging operation with a non-zero `native_fee` permanently locks STRK in the contract with no recovery path.

### Finding Description

In `starknet/src/omni_bridge.cairo`, the `init_transfer` function accepts a `native_fee` parameter denominated in STRK. When `native_fee > 0`, the contract pulls STRK from the caller directly into itself: [1](#0-0) 

```cairo
if native_fee > 0 {
    let native_token = self.strk_token_address.read();
    let success = IERC20Dispatcher { contract_address: native_token }
        .transfer_from(caller, get_contract_address(), native_fee.into());
    assert(success, 'ERR_FEE_TRANSFER_FAILED');
}
```

The entire `IOmniBridge` interface defines only these callable functions: [2](#0-1) 

None of them — `log_metadata`, `deploy_token`, `fin_transfer`, `init_transfer`, `upgrade_token`, `set_pause_flags`, `pause_all`, and the three view functions — provide any mechanism to withdraw or distribute the accumulated STRK tokens. The `upgrade` function only changes the class hash; it does not transfer tokens. [3](#0-2) 

By contrast, the NEAR bridge handles native fees through a storage-balance accounting system that can be consumed and refunded. The StarkNet contract has no equivalent accounting or disbursement mechanism for the STRK it collects.

### Impact Explanation

Every call to `init_transfer` with `native_fee > 0` permanently deposits STRK into the contract with no exit path. The intended purpose of `native_fee` is to compensate relayers for their NEAR-side gas costs, but since there is no `claim_fee` or `withdraw` function, relayers can never receive this compensation. The STRK balance grows monotonically and is irrecoverably locked. This matches the allowed impact: **permanent freezing / irrecoverable lock of protocol funds in bridge flows**.

### Likelihood Explanation

Any unprivileged bridge user calling `init_transfer` with `native_fee > 0` triggers the accumulation. This is a normal, expected usage path — users pay native fees to incentivize relayers. No special conditions or attacker knowledge are required; the lock occurs on every such call.

### Recommendation

Add a privileged `withdraw_native_fees` (or equivalent `claim_fee`) function to the StarkNet bridge that allows an authorized role (e.g., `DEFAULT_ADMIN_ROLE` or a designated fee-recipient address) to transfer accumulated STRK out of the contract. Alternatively, mirror the NEAR bridge's approach of tracking per-relayer fee balances and allowing relayers to claim their earned fees directly.

### Proof of Concept

1. User calls `init_transfer` on the StarkNet `OmniBridge` with `native_fee = 1_000_000` (1 STRK).
2. The contract executes `IERC20Dispatcher { contract_address: strk_token }.transfer_from(caller, get_contract_address(), 1_000_000)`.
3. The STRK balance of the contract increases by 1,000,000.
4. The relayer completes the bridge operation on NEAR but has no way to claim the STRK fee on StarkNet — no `claim_fee`, `withdraw`, or equivalent function exists in the contract.
5. Repeating across all bridge users causes unbounded, irrecoverable STRK accumulation in the contract. [4](#0-3)

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

**File:** starknet/src/omni_bridge.cairo (L390-395)
```text
    impl UpgradeableImpl of IUpgradeable<ContractState> {
        fn upgrade(ref self: ContractState, new_class_hash: ClassHash) {
            self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
            self.upgradeable.upgrade(new_class_hash);
        }
    }
```
