### Title
Unregistered Token Accepted in `init_transfer` / `initTransfer` Causes Irrecoverable Fund Lock — (`starknet/src/omni_bridge.cairo`, `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

The `init_transfer` function on StarkNet and `initTransfer` on EVM accept any arbitrary token address without verifying that the token is registered in the bridge's token registry. When a user submits a transfer with an unregistered token, the tokens are locked or burned on the source chain, but the NEAR-side finalization panics with `TokenDecimalsNotFound` because the token has no registered decimals entry. There is no built-in recovery path, so the user's funds are permanently locked in the bridge contract.

---

### Finding Description

**StarkNet (`starknet/src/omni_bridge.cairo`, lines 281–331):**

`init_transfer` accepts any `token_address: ContractAddress`. It checks only `is_bridge_token` (which tests `starknet_to_near_token.read(token_address).len() > 0`) to decide whether to burn or lock. There is no check that the token is registered in the bridge's cross-chain token mapping. [1](#0-0) 

If `token_address` is not a bridge-deployed token, the function calls `transfer_from(caller, get_contract_address(), amount)` and emits an `InitTransfer` event — with no validation that the token is known to NEAR. [2](#0-1) 

**EVM (`evm/src/omni-bridge/contracts/OmniBridge.sol`, lines 373–437):**

`initTransfer` similarly accepts any `tokenAddress`. For tokens not in `customMinters` or `isBridgeToken`, it calls `safeTransferFrom` to lock the token — again with no check that the token is registered in `ethToNearToken`. [3](#0-2) 

**NEAR-side rejection (`near/omni-bridge/src/lib.rs`, lines 715–718):**

When the relayer submits the proof to NEAR's `fin_transfer_callback`, the contract immediately panics if the token has no registered decimals entry: [4](#0-3) 

The NEAR transaction fails. The source-chain tokens are already locked/burned with no refund or cancellation mechanism in either the StarkNet or EVM bridge contracts.

---

### Impact Explanation

Any user who calls `init_transfer` (StarkNet) or `initTransfer` (EVM) with a token address that is not registered in the bridge's token registry will have their tokens permanently locked in the bridge contract. Neither the StarkNet nor the EVM bridge exposes a built-in rescue or recovery function. Recovery requires an admin-initiated contract upgrade, which is not guaranteed and is not part of the protocol's normal operation. This constitutes an irrecoverable lock of user funds in bridge flows.

**Allowed impact matched:** Critical — Permanent freezing, irrecoverable lock of user funds in bridge flows.

---

### Likelihood Explanation

The entry path is fully unprivileged: any token holder can call `init_transfer` / `initTransfer` directly. The scenario is reachable by:
- A user who mistakenly passes the wrong token address.
- A user who holds a token that was previously registered but has since been removed from the registry.
- A malicious actor who socially engineers a victim into approving and calling the bridge with an unregistered token.

Likelihood is **medium-low** in practice (requires user action with an unregistered token), but the impact when triggered is irreversible without admin intervention.

---

### Recommendation

Add a registration check at the start of `init_transfer` (StarkNet) and `initTransfer` (EVM) to verify that the supplied token address is known to the bridge before accepting any token transfer:

- **StarkNet**: Assert `self.starknet_to_near_token.read(token_address).len() > 0` (i.e., `is_bridge_token(token_address)`) **or** that the token has a corresponding entry in the NEAR-side registry (communicated via a whitelist or the `near_to_starknet_token` mapping).
- **EVM**: Assert `bytes(ethToNearToken[tokenAddress]).length > 0` before the lock/burn branch, rejecting transfers for tokens with no registered NEAR counterpart.

---

### Proof of Concept

1. Deploy or obtain any ERC20-compatible token `T` on StarkNet that is **not** registered via `deploy_token`.
2. Approve the StarkNet bridge to spend `T`.
3. Call `init_transfer(T, 1000, 0, 0, "victim.near", "")`.
4. The bridge calls `T.transfer_from(caller, bridge, 1000)` — succeeds. Tokens are now held by the bridge.
5. An `InitTransfer` event is emitted with `token_address = T`.
6. A relayer picks up the event and calls NEAR's `fin_transfer` with the corresponding proof.
7. NEAR's `fin_transfer_callback` executes `self.token_decimals.get(&init_transfer.token).near_expect(BridgeError::TokenDecimalsNotFound)` — **panics**.
8. The NEAR transaction reverts. The 1000 units of `T` remain locked in the StarkNet bridge with no recovery path. [5](#0-4) [4](#0-3)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L404-413)
```text
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
```

**File:** near/omni-bridge/src/lib.rs (L715-718)
```rust
        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);
```
