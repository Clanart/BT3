### Title
Starknet `init_transfer` Accepts Unregistered Token Addresses, Causing Irrecoverable Fund Lock — (`starknet/src/omni_bridge.cairo`)

---

### Summary

The Starknet `init_transfer` function accepts any arbitrary `token_address` without verifying it is registered in the bridge's token mapping. Tokens are locked in the bridge contract and an `InitTransfer` event is emitted, but the NEAR side cannot finalize the transfer for an unregistered token, leaving user funds permanently frozen with no on-chain recovery path.

---

### Finding Description

`init_transfer` in `starknet/src/omni_bridge.cairo` performs only two input checks before locking tokens:

```cairo
assert(amount > 0, 'ERR_ZERO_AMOUNT');
assert(fee < amount, 'ERR_INVALID_FEE');
``` [1](#0-0) 

It then branches on `is_bridge_token(token_address)` — which only returns `true` for tokens deployed by the bridge itself (tracked in `starknet_to_near_token`). For any other token, it falls through to a plain `transfer_from` that locks the tokens in the bridge:

```cairo
} else {
    let success = IERC20Dispatcher { contract_address: token_address }
        .transfer_from(caller, get_contract_address(), amount.into());
    assert(success, 'ERR_TRANSFER_FROM_FAILED');
}
``` [2](#0-1) 

There is **no check** that `token_address` exists in `near_to_starknet_token` (the registry of tokens known to both Starknet and NEAR). After locking, the function emits `InitTransfer` with the unverified address: [3](#0-2) 

The NEAR bridge is the sole authority for finalizing the transfer. It reads the emitted event via a Starknet prover and calls `fin_transfer_callback`, which looks up `token_address_to_id` for the Starknet address. If the token is not registered, the callback panics with `TokenNotFound`: [4](#0-3) 

The Starknet bridge has no `withdraw`, `refund`, or emergency-recovery function. The only path for tokens to leave the bridge is `fin_transfer`, which requires a valid NEAR MPC signature. Without NEAR-side registration, no such signature can be produced, and the tokens are permanently locked.

The EVM `initTransfer` has the same structural gap — it accepts any ERC20 address and locks it without checking `nearToEthToken`: [5](#0-4) 

The EVM `SECURITY.md` acknowledges "fee-on-transfer tokens not supported" but does not acknowledge unregistered-token permanent lock as a known issue: [6](#0-5) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds** (Critical impact class).

Any user who calls `init_transfer` with a token address not registered in the bridge's `near_to_starknet_token` mapping will have their tokens locked in the Starknet bridge contract with no on-chain recovery path. The NEAR side cannot finalize the transfer, no MPC signature can be produced for an unregistered token, and the Starknet contract has no refund mechanism. Tokens are permanently frozen unless an admin manually registers the token on NEAR and a relayer retries the proof — neither of which is guaranteed or automated.

---

### Likelihood Explanation

**Medium.** The entry path is fully permissionless — any user can call `init_transfer` directly. Realistic triggers include:

1. A user bridges a token that has not yet been registered via `deploy_token` (e.g., a newly listed token, or a token whose `log_metadata` → `deploy_token` flow has not yet completed on NEAR).
2. A frontend bug or misconfiguration supplies a wrong token address.
3. A user integrating the bridge contract directly (without the official SDK) passes an unregistered address.

The protocol's own documentation confirms `init_transfer` is a public, permissionless entry point: [7](#0-6) 

---

### Recommendation

Add a registry check at the top of `init_transfer` before any token transfer occurs:

```cairo
// Require token is registered in the bridge mapping
let token_id_hash = compute_keccak_byte_array(@self.starknet_to_near_token.read(token_address));
assert(!self.near_to_starknet_token.read(token_id_hash).is_zero() 
    || self.is_bridge_token(token_address), 
    'ERR_TOKEN_NOT_REGISTERED');
```

Alternatively, maintain a separate `registered_tokens: Map<ContractAddress, bool>` set that is populated by `deploy_token` and any admin token-registration function, and assert membership at the start of `init_transfer`. Apply the same fix to EVM `initTransfer` by checking `nearToEthToken[tokenAddress] != address(0) || isBridgeToken[tokenAddress] || customMinters[tokenAddress] != address(0)` before locking tokens.

---

### Proof of Concept

1. Deploy or obtain any ERC20 token on Starknet that is **not** registered via `deploy_token` on the Starknet bridge (i.e., `near_to_starknet_token` has no entry for it).
2. Approve the Starknet bridge to spend `amount` of this token.
3. Call `init_transfer(unregistered_token, amount, 0, 0, "victim.near", "")`.
4. The `transfer_from` succeeds — tokens are now in the bridge contract.
5. `InitTransfer` event is emitted with `token_address = unregistered_token`.
6. A relayer submits the Starknet proof to the NEAR bridge's `fin_transfer`.
7. `fin_transfer_callback` calls `get_token_id(&unregistered_starknet_address)` → panics `ERR_TOKEN_NOT_FOUND`.
8. The callback reverts; `finalised_transfers` is not updated; but the tokens remain locked in the Starknet bridge.
9. No MPC signature can be produced for an unregistered token; no `fin_transfer` on Starknet can release them.
10. Tokens are permanently frozen. [8](#0-7) [9](#0-8)

### Citations

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

**File:** near/omni-types/src/errors.rs (L58-60)
```rust
    TokenNotFound,
    TokenNotMigrated,
    TokenNotRegistered,
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L406-412)
```text
            } else {
                IERC20(tokenAddress).safeTransferFrom(
                    msg.sender,
                    address(this),
                    amount
                );
            }
```

**File:** evm/SECURITY.md (L7-7)
```markdown
- **Fee-on-transfer tokens not supported**: `initTransfer` emits the requested `amount`, not the actual received balance. Fee-on-transfer and rebasing tokens are intentionally unsupported
```

**File:** starknet/CLAUDE.md (L16-16)
```markdown
| `init_transfer` | Send tokens from Starknet to another chain | Public |
```

**File:** near/omni-bridge/src/lib.rs (L670-696)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
        require!(
            args.storage_deposit_actions.len() <= 3,
            BridgeError::InvalidStorageAccountsLen.as_ref()
        );
        let mut main_promise = self.verify_proof(args.chain_kind, args.prover_args);

        let mut attached_deposit = env::attached_deposit();

        for action in &args.storage_deposit_actions {
            main_promise =
                main_promise.and(Self::check_or_pay_ft_storage(action, &mut attached_deposit));
        }

        main_promise.then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(attached_deposit)
                .with_static_gas(FIN_TRANSFER_CALLBACK_GAS)
                .fin_transfer_callback(
                    &args.storage_deposit_actions,
                    env::predecessor_account_id(),
                ),
        )
    }
```
