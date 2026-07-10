### Title
Pausable Non-Bridge ERC20 Tokens Can Permanently Freeze User Funds in EVM OmniBridge - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

### Summary
`OmniBridge.finTransfer()` calls `IERC20(payload.tokenAddress).safeTransfer()` for non-bridge ERC20 tokens without any mechanism to recover origin-chain funds if the destination-chain transfer is permanently blocked. Many widely-used ERC20 tokens (USDC, USDT, etc.) implement a pause mechanism. If such a token is paused at the time of finalization — or permanently paused/abandoned — the `finTransfer` call reverts on every attempt, and the user's funds locked on the origin chain (NEAR) have no recovery path.

### Finding Description
`finTransfer` in `OmniBridge.sol` handles non-bridge ERC20 tokens via the `else` branch:

```solidity
} else {
    IERC20(payload.tokenAddress).safeTransfer(
        payload.recipient,
        payload.amount
    );
}
```

The nonce is marked used at line 287 **before** the transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;
```

Because Solidity reverts atomically, if `safeTransfer` reverts (e.g., token paused), the nonce marking also reverts, so the nonce is not permanently consumed. The relayer can retry. However, the user's tokens were already locked/burned on NEAR by `init_transfer` (via `ft_on_transfer` → `init_transfer_internal`). There is **no cancel or refund path** on the NEAR side for a transfer whose destination-chain finalization never succeeds.

If the bridged ERC20 token is:
- **Temporarily paused**: funds are frozen until the token is unpaused; relayer can retry.
- **Permanently paused or contract abandoned**: `finTransfer` will revert on every attempt forever, and the user's NEAR-side tokens are irrecoverably locked.

The same pattern exists in the StarkNet bridge (`starknet/src/omni_bridge.cairo`), where `_set_transfer_finalised` is called before the `transfer` call, and a failed `transfer` reverts the whole transaction — but again, no NEAR-side recovery exists.

### Impact Explanation
A user who bridges a pausable ERC20 token from NEAR to EVM (or StarkNet) has their origin-chain tokens locked at `init_transfer` time. If the destination-chain token is permanently paused, the user permanently loses access to those funds. This is an irrecoverable lock of user assets in the bridge flow, matching the **Critical/High: Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds** impact class.

### Likelihood Explanation
Many real-world ERC20 tokens supported by the bridge (USDC, USDT, and others) implement `whenNotPaused` on their `transfer` function. A token pause can be triggered by the token issuer for regulatory, security, or operational reasons outside the bridge's control. While a permanent pause is less common than a temporary one, even a temporary pause causes a prolonged freeze with no user-facing escape hatch. The bridge explicitly supports arbitrary non-bridge ERC20 tokens with no filtering for pausable tokens, making this a realistic scenario for any token that has ever been paused.

### Recommendation
1. **Add a timeout-based cancellation path on NEAR**: Allow a user (or relayer) to cancel a pending `init_transfer` and reclaim origin-chain tokens if the destination-chain finalization has not been confirmed within a configurable window.
2. **Document and warn**: At minimum, document that bridging pausable ERC20 tokens carries the risk of permanent fund lock if the token is permanently paused.
3. **Allowlist tokens**: Consider restricting non-bridge ERC20 support to a vetted allowlist of tokens known not to have blocking pause mechanisms.

### Proof of Concept
1. User calls `ft_transfer_call` on NEAR with a USDC-equivalent token → `ft_on_transfer` → `init_transfer_internal` → tokens locked in NEAR bridge contract. [1](#0-0) 
2. MPC signs the transfer; relayer submits `finTransfer` on EVM with `payload.tokenAddress = <pausable ERC20>`.
3. The token issuer pauses the ERC20 (e.g., regulatory freeze).
4. `IERC20(payload.tokenAddress).safeTransfer(payload.recipient, payload.amount)` reverts on every call. [2](#0-1) 
5. Because the nonce is marked before the transfer but reverts atomically, the nonce is never consumed — yet the NEAR-side lock has no corresponding unlock. [3](#0-2) 
6. No `cancel_transfer` or refund function exists on the NEAR bridge contract; `pending_transfers` and `finalised_transfers` have no user-callable escape path. [4](#0-3) 
7. If the pause is permanent, the user's NEAR tokens are irrecoverably locked.

The same issue applies to the StarkNet bridge's `fin_transfer`, where `_set_transfer_finalised` is called before the `IERC20Dispatcher.transfer` call, and a failed transfer reverts the nonce marking but leaves origin-chain funds with no recovery path. [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L220-243)
```rust
pub struct Contract {
    pub factories: LookupMap<ChainKind, OmniAddress>,
    pub pending_transfers: LookupMap<TransferId, TransferMessageStorage>,
    pub finalised_transfers: LookupSet<TransferId>,
    pub finalised_utxo_transfers: LookupSet<UnifiedTransferId>,
    pub fast_transfers: LookupMap<FastTransferId, FastTransferStatusStorage>,
    pub token_id_to_address: LookupMap<(ChainKind, AccountId), OmniAddress>,
    pub token_address_to_id: LookupMap<OmniAddress, AccountId>,
    pub token_decimals: LookupMap<OmniAddress, Decimals>,
    pub deployed_tokens: LookupSet<AccountId>,
    pub deployed_tokens_v2: LookupMap<AccountId, ChainKind>,
    pub token_deployer_accounts: LookupMap<ChainKind, AccountId>,
    pub mpc_signer: AccountId,
    pub current_origin_nonce: Nonce,
    // We maintain a separate nonce for each chain to optimize the storage usage on Solana by reducing the gaps.
    pub destination_nonces: LookupMap<ChainKind, Nonce>,
    pub accounts_balances: LookupMap<AccountId, StorageBalance>,
    pub wnear_account_id: AccountId,
    pub provers: UnorderedMap<ChainKind, AccountId>,
    pub init_transfer_promises: LookupMap<AccountId, CryptoHash>,
    pub utxo_chain_connectors: HashMap<ChainKind, UTXOChainConfig>,
    pub migrated_tokens: LookupMap<AccountId, AccountId>,
    pub locked_tokens: LookupMap<(ChainKind, AccountId), u128>,
}
```

**File:** near/omni-bridge/src/lib.rs (L252-263)
```rust
    #[pause(except(roles(Role::DAO, Role::UnrestrictedDeposit)))]
    pub fn ft_on_transfer(&mut self, sender_id: AccountId, amount: U128, msg: String) {
        let token_id = env::predecessor_account_id();
        let parsed_msg: BridgeOnTransferMsg = serde_json::from_str(&msg)
            .or_else(|_| serde_json::from_str(&msg).map(BridgeOnTransferMsg::InitTransfer))
            .near_expect(BridgeError::ParseMsg);

        // We can't trust sender_id to pay for storage as it can be spoofed.
        let signer_id = env::signer_account_id();
        let promise_or_promise_index_or_value = match parsed_msg {
            BridgeOnTransferMsg::InitTransfer(init_transfer_msg) => {
                self.init_transfer(sender_id, signer_id, token_id, amount, init_transfer_msg)
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-288)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L350-355)
```text
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** starknet/src/omni_bridge.cairo (L247-263)
```text
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
```
