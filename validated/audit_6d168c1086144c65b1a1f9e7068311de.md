### Title
No Cancellation Path for Pending Transfers Causes Permanent Fund Lock When Destination Chain Bridge Becomes Irrecoverable — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-to-foreign-chain transfer with a non-zero fee, their tokens are burned or locked on NEAR and a `TransferMessage` is written to `pending_transfers`. The **only** code path that removes this record is `claim_fee_callback`, which requires a valid on-chain proof from the destination chain. If the destination chain's bridge contract is permanently paused, bricked, or its prover becomes irrecoverable, no such proof can ever be produced. Because the contract exposes no cancellation or emergency-refund function, the user's tokens are permanently frozen with no recovery path.

---

### Finding Description

**Transfer initiation burns tokens and stores state atomically.**

When `ft_on_transfer` is called with an `InitTransfer` message, `init_transfer` increments the nonce, constructs a `TransferMessage`, and calls `init_transfer_internal`, which returns `U128(0)` — signalling to the NEP-141 token contract that zero tokens should be refunded. The tokens are therefore irrevocably burned or locked at this point. [1](#0-0) 

**For non-zero-fee transfers, the only removal path is `claim_fee_callback`.**

`sign_transfer_callback` only removes the `TransferMessage` from `pending_transfers` when `fee.is_zero()`. For all other transfers the record persists. [2](#0-1) 

`claim_fee_callback` is the sole remaining removal path. It calls `remove_transfer_message` only after successfully decoding a `ProverResult::FinTransfer` returned by `verify_proof` — i.e., only after a valid proof from the destination chain is verified. [3](#0-2) 

**No cancellation or emergency-refund function exists.**

Searching the entire contract, there is no `cancel_transfer`, `refund_transfer`, or equivalent function accessible to users or even to the DAO role that would remove a `pending_transfers` entry and return tokens to the sender. The DAO can adjust `locked_tokens` balances via `set_locked_tokens`, but this does not refund the burned tokens or remove the pending transfer record. [4](#0-3) 

**The bridge supports many independent destination chains, each with its own bridge contract and prover.**

The contract maps each `ChainKind` to a separate prover and factory address. A failure of any single destination chain's bridge (e.g., permanent pause of `OmniBridge.sol` on Arbitrum, a Wormhole guardian set that stops attesting for a chain, or a light-client that can no longer advance) makes it impossible to generate the proof required by `claim_fee_callback`. [5](#0-4) [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

A user who bridges tokens from NEAR to a destination chain that subsequently becomes permanently irrecoverable loses their tokens with no recourse:

- Tokens are burned/locked on NEAR at `init_transfer` time.
- The `TransferMessage` in `pending_transfers` can never be removed.
- No admin, DAO, or user-callable function can cancel the transfer and return tokens.
- The user cannot re-initiate the transfer to a different chain; the original tokens are gone.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** The bridge already supports more than ten destination chains (ETH, ARB, BASE, BNB, SOL, STRK, ABS, FOGO, BTC, ZCASH, POL, HyperEVM). Each chain has its own bridge contract with an independent pause mechanism (`PAUSED_FIN_TRANSFER`, `PAUSED_INIT_TRANSFER` flags in `OmniBridge.sol`), its own Wormhole or light-client prover dependency, and its own admin key set. [7](#0-6) 

Historical precedent (bridge pauses, oracle failures, guardian-set migrations) shows that individual chain bridges do become temporarily or permanently unavailable. The contest README explicitly acknowledges Chainlink and DSU integration risks. A permanent pause of even one destination chain's `finTransfer` path is sufficient to trigger this vulnerability for all in-flight transfers to that chain.

---

### Recommendation

1. **Add a user-callable `cancel_transfer` function** that, after a configurable timeout (e.g., 7 days with no `claim_fee` proof), allows the original sender to reclaim their tokens by re-minting or unlocking them on NEAR and removing the `pending_transfers` entry.
2. **Alternatively**, add a DAO-callable emergency rescue function that can remove a stuck `pending_transfers` entry and credit the sender's storage balance with the equivalent token amount, enabling a manual refund flow.
3. **Document the risk** clearly: users should understand that initiating a transfer to a destination chain whose bridge is paused or broken will result in funds being locked until the destination chain recovers or an emergency mechanism is invoked.

---

### Proof of Concept

1. Alice calls `ft_transfer_call` on a NEAR token contract, sending 1000 USDC to the `omni-bridge` contract with `msg = InitTransfer { recipient: "arb:0xAlice", fee: 10, native_token_fee: 0, ... }`.
2. `ft_on_transfer` → `init_transfer` → `init_transfer_internal` executes. The 1000 USDC are burned. `pending_transfers[{Near, nonce}]` is written. `ft_on_transfer` returns `U128(0)` — no refund.
3. A relayer calls `sign_transfer`. MPC signs the payload. `sign_transfer_callback` fires: because `fee = 10 ≠ 0`, the transfer is **not** removed from `pending_transfers`. A `SignTransferEvent` is emitted. [2](#0-1) 

4. The Arbitrum `OmniBridge` contract is permanently paused (`PAUSED_FIN_TRANSFER` set). The relayer cannot call `finTransfer` on Arbitrum. No `FinTransfer` event is ever emitted on Arbitrum. [8](#0-7) 

5. Without a `FinTransfer` event on Arbitrum, no proof can be submitted to `claim_fee` on NEAR. `claim_fee_callback` can never execute. `remove_transfer_message` is never called. [9](#0-8) 

6. Alice's 1000 USDC are permanently burned on NEAR. There is no function she or the DAO can call to recover them. The `pending_transfers` entry persists indefinitely.

### Citations

**File:** near/omni-bridge/src/lib.rs (L221-243)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L536-557)
```rust
        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L648-668)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1057-1134)
```rust
    pub fn claim_fee(&mut self, #[serializer(borsh)] args: ClaimFeeArgs) -> Promise {
        self.verify_proof(args.chain_kind, args.prover_args).then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(env::attached_deposit())
                .with_static_gas(CLAIM_FEE_CALLBACK_GAS)
                .claim_fee_callback(&env::predecessor_account_id()),
        )
    }

    #[private]
    #[payable]
    pub fn claim_fee_callback(
        &mut self,
        #[serializer(borsh)] predecessor_account_id: &AccountId,
        #[callback_result]
        #[serializer(borsh)]
        call_result: Result<ProverResult, PromiseError>,
    ) -> PromiseOrValue<()> {
        let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };

        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);

        if let Some(origin_transfer_id) = transfer_message.origin_transfer_id.clone() {
            let mut fast_transfer = FastTransfer::from_transfer(
                transfer_message.clone(),
                self.get_token_id(&transfer_message.token),
            );
            fast_transfer.transfer_id = origin_transfer_id;

            if let Some(fast_transfer_status) = self.get_fast_transfer_status(&fast_transfer.id()) {
                // For fast transfers we need to wait for finalization of the first leg (Origin chain -> Near) before allowing fee claim.
                // This confirms that fast transfer was executed with correct parameters.
                // Othewise malicious relayer can create a fast transfer with arbitrary high fee and claim it here.
                if fast_transfer_status.finalised {
                    self.remove_fast_transfer(&fast_transfer.id());
                } else {
                    env::panic_str(BridgeError::FastTransferNotFinalised.to_string().as_str());
                }
            }
        }

        let token = self.get_token_id(&transfer_message.token);
        let token_address = self
            .get_token_address(transfer_message.get_destination_chain(), token.clone())
            .unwrap_or_else(|| {
                env::panic_str(BridgeError::FailedToGetTokenAddress.to_string().as_str())
            });

        let denormalized_amount = Self::denormalize_amount(
            fin_transfer.amount.0,
            self.token_decimals
                .get(&token_address)
                .near_expect(BridgeError::TokenDecimalsNotFound),
        );
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;

        self.send_fee_internal(&transfer_message, fee_recipient, fee)
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L38-44)
```rust
    #[access_control_any(roles(Role::DAO, Role::TokenLockController))]
    pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
        for arg in args {
            self.locked_tokens
                .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L50-56)
```text
    bytes32 public constant PAUSABLE_ADMIN_ROLE =
        keccak256("PAUSABLE_ADMIN_ROLE");
    uint256 constant UNPAUSED_ALL = 0;
    uint256 constant PAUSED_INIT_TRANSFER = 1 << 0;
    uint256 constant PAUSED_FIN_TRANSFER = 1 << 1;
    uint256 constant PAUSED_DEPLOY_TOKEN = 1 << 2;

```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-287)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L548-557)
```text
    function pause(uint256 flags) external onlyRole(DEFAULT_ADMIN_ROLE) {
        _pause(flags);
    }

    function pauseAll() external onlyRole(PAUSABLE_ADMIN_ROLE) {
        uint256 flags = PAUSED_FIN_TRANSFER |
            PAUSED_INIT_TRANSFER |
            PAUSED_DEPLOY_TOKEN;
        _pause(flags);
    }
```
