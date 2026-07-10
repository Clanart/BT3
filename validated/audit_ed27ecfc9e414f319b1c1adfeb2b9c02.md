### Title
`init_transfer` Accepts Unregistered Tokens Without Validation, Causing Permanent Fund Lock - (File: starknet/src/omni_bridge.cairo)

### Summary
The `init_transfer` function in the StarkNet `OmniBridge` contract accepts any arbitrary `token_address` without verifying that the token is registered in the bridge's token mapping. Tokens that are not registered on the NEAR side will be permanently locked in the bridge contract with no recovery path.

### Finding Description
The `init_transfer` function in `starknet/src/omni_bridge.cairo` performs only three input checks before accepting tokens:

1. Not paused
2. `amount > 0`
3. `fee < amount` [1](#0-0) 

It then branches on `is_bridge_token(token_address)` solely to decide whether to **burn** (bridge-deployed tokens) or **transfer_from** (native tokens). Crucially, it does **not** reject the call when `token_address` is neither a bridge-deployed token nor a registered native token: [2](#0-1) 

`is_bridge_token` returns `true` only for tokens deployed by the bridge (those written into `starknet_to_near_token` during `deploy_token`): [3](#0-2) 

Native StarkNet tokens (e.g., USDC, STRK) that have not been registered on the NEAR side via `bind_token` are **not** present in `starknet_to_near_token`. When a user calls `init_transfer` with such a token, the `transfer_from` succeeds, an `InitTransfer` event is emitted, but the NEAR bridge will reject any finalization attempt because the token has no registered decimals or address mapping.

On the NEAR side, `fin_transfer_callback` requires the token to be registered in `token_decimals`: [4](#0-3) 

There is no admin rescue or withdrawal function in the StarkNet contract for tokens locked via `init_transfer`. The only state-mutating privileged functions are `set_pause_flags`, `pause_all`, `upgrade_token`, and `upgrade` — none of which can recover locked ERC-20 balances. [5](#0-4) 

### Impact Explanation
Any user who calls `init_transfer` with a token that is not registered in the NEAR bridge will have their tokens permanently locked in the StarkNet bridge contract. There is no on-chain recovery path. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user funds in bridge or vault flows.**

### Likelihood Explanation
The entry point is fully public and requires no privilege. Any token holder can call `init_transfer` with any ERC-20 token address. The risk is realistic because:
- The StarkNet contract provides no on-chain registry query for users to verify token eligibility before calling.
- A user who holds a token that was previously registered but later de-listed on NEAR (or a token that was never registered) can unknowingly lock funds.
- The function signature is identical for registered and unregistered tokens, providing no user-facing signal.

### Recommendation
Add an explicit check in `init_transfer` that the `token_address` is a known bridge token (either bridge-deployed or registered as a native token). For example:

```cairo
fn init_transfer(..., token_address: ContractAddress, ...) {
    ...
    let near_token_id = self.starknet_to_near_token.read(token_address);
    assert(near_token_id.len() > 0, 'ERR_TOKEN_NOT_REGISTERED');
    ...
}
```

This mirrors the pattern used in `fin_transfer`, which validates the token is a known bridge token before minting or transferring: [6](#0-5) 

### Proof of Concept
1. Deploy any ERC-20 token `FakeToken` on StarkNet that is **not** registered in the NEAR bridge.
2. Approve the StarkNet `OmniBridge` contract to spend `FakeToken`.
3. Call `init_transfer(fake_token_address, 1000, 0, 0, "alice.near", "")`.
4. The call succeeds: `transfer_from` moves 1000 `FakeToken` into the bridge contract, and an `InitTransfer` event is emitted.
5. A relayer attempts to call `fin_transfer` on NEAR. The NEAR bridge panics at `BridgeError::TokenDecimalsNotFound` because `FakeToken` has no entry in `token_decimals`.
6. The 1000 `FakeToken` are permanently locked in the StarkNet bridge contract with no recovery mechanism.

### Citations

**File:** starknet/src/omni_bridge.cairo (L256-263)
```text
            if self.is_bridge_token(payload.token_address) {
                IBridgeTokenDispatcher { contract_address: payload.token_address }
                    .mint(payload.recipient, payload.amount.into());
            } else {
                let success = IERC20Dispatcher { contract_address: payload.token_address }
                    .transfer(payload.recipient, payload.amount.into());
                assert(success, 'ERR_TRANSFER_FAILED');
            }
```

**File:** starknet/src/omni_bridge.cairo (L281-307)
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
```

**File:** starknet/src/omni_bridge.cairo (L333-395)
```text
        fn upgrade_token(
            ref self: ContractState, token_address: ContractAddress, new_class_hash: ClassHash,
        ) {
            self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
            assert(self.is_bridge_token(token_address), 'ERR_NOT_BRIDGE_TOKEN');

            let upgradeable = IUpgradeableDispatcher { contract_address: token_address };
            upgradeable.upgrade(new_class_hash);
        }

        fn set_pause_flags(ref self: ContractState, flags: u8) {
            self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
            let old_flags = self.pause_flags.read();
            self.pause_flags.write(flags);

            self
                .emit(
                    Event::PauseStateChanged(
                        PauseStateChanged {
                            old_flags, new_flags: flags, admin: get_caller_address(),
                        },
                    ),
                );
        }

        fn pause_all(ref self: ContractState) {
            self.accesscontrol.assert_only_role(PAUSER_ROLE);
            let old_flags = self.pause_flags.read();
            self.pause_flags.write(PAUSE_ALL);

            self
                .emit(
                    Event::PauseStateChanged(
                        PauseStateChanged {
                            old_flags, new_flags: PAUSE_ALL, admin: get_caller_address(),
                        },
                    ),
                );
        }

        fn get_token_address(self: @ContractState, token_id: ByteArray) -> ContractAddress {
            let token_id_hash = compute_keccak_byte_array(@token_id);
            self.near_to_starknet_token.read(token_id_hash)
        }

        fn is_bridge_token(self: @ContractState, token_address: ContractAddress) -> bool {
            self.starknet_to_near_token.read(token_address).len() > 0
        }

        fn is_transfer_finalised(self: @ContractState, nonce: u64) -> bool {
            let (slot, bit) = _nonce_slot_and_bit(nonce);
            let bitmap: u256 = self.completed_transfers.read(slot).into();
            bitmap & bit != 0
        }
    }

    #[abi(embed_v0)]
    impl UpgradeableImpl of IUpgradeable<ContractState> {
        fn upgrade(ref self: ContractState, new_class_hash: ClassHash) {
            self.accesscontrol.assert_only_role(DEFAULT_ADMIN_ROLE);
            self.upgradeable.upgrade(new_class_hash);
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L715-718)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
