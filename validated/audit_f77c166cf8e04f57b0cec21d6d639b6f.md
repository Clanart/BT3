Now I have all the information needed for a complete analysis. Let me trace the full exploit path.

**Key facts confirmed:**

1. `OmniAddress::from_str` line 393 of `near/omni-types/src/lib.rs`:
   ```rust
   let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));
   ```
   No `:` → defaults to `"eth"` chain.

2. `parse_init_transfer` line 61 of `near/omni-types/src/starknet/events.rs`:
   ```rust
   let recipient: OmniAddress = recipient_str.parse().map_err(stringify)?;
   ```
   Raw ByteArray string passed directly to `OmniAddress::from_str`.

3. `H160::from_str` (`hex_types.rs`, `padded = false`): requires **exactly 40 hex chars**. A 40-char lowercase hex string (e.g., `deadbeefdeadbeefdeadbeefdeadbeefdeadbeef`) is also a valid NEAR account ID (≤64 chars, only `[a-f0-9]`).

4. `fin_transfer_callback` line 734 of `near/omni-bridge/src/lib.rs`:
   ```rust
   if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
       self.process_fin_transfer_to_near(...)
   } else {
       self.process_fin_transfer_to_other_chain(...);
   }
   ```

5. `unlock_tokens_if_needed` / `lock_tokens_if_needed` in `near/omni-bridge/src/token_lock.rs` lines 96–120: both are **no-ops** when `get_token_origin_chain(token_id) == chain_kind` or when no locked-token entry exists for that chain. For an ETH-native token bridged to StarkNet, `unlock_tokens_if_needed(Strk, weth, amount)` → no-op (no Strk entry), and `lock_tokens_if_needed(Eth, weth, fee)` → no-op (Eth is origin chain).

---

### Title
Recipient-chain mismatch via `OmniAddress::from_str` ETH default enables StarkNet→ETH fund misrouting — (`near/omni-types/src/lib.rs`, `near/omni-types/src/starknet/events.rs`)

### Summary

`OmniAddress::from_str` silently defaults to the ETH chain when the input string contains no `:` separator. The StarkNet `init_transfer` function accepts an arbitrary `ByteArray` recipient with no format enforcement. A 40-character lowercase hex string is simultaneously a valid NEAR account ID and a valid `H160` EVM address. An attacker can exploit this to route a StarkNet→NEAR transfer to the ETH chain instead, causing permanent fund loss or theft of ETH-pool tokens.

### Finding Description

`OmniAddress::from_str` in `near/omni-types/src/lib.rs` uses:

```rust
let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));
``` [1](#0-0) 

When no `:` is present, the entire string is treated as an ETH address. `H160::from_str` (with `padded = false`) requires exactly 40 hex characters — no more, no less. [2](#0-1) 

A 40-character lowercase hex string such as `deadbeefdeadbeefdeadbeefdeadbeefdeadbeef` satisfies both:
- NEAR account ID rules (≤64 chars, only `[a-z0-9._-]`)
- `H160::from_str` (exactly 40 hex chars)

The StarkNet bridge accepts any `ByteArray` as `recipient` with no validation: [3](#0-2) 

`parse_init_transfer` passes the raw string directly to `OmniAddress::from_str`: [4](#0-3) 

`fin_transfer_callback` then routes based solely on the parsed variant: [5](#0-4) 

A 40-char hex recipient without prefix parses as `OmniAddress::Eth(H160(...))`, so the transfer goes to `process_fin_transfer_to_other_chain` instead of `process_fin_transfer_to_near`.

Inside `process_fin_transfer_to_other_chain`, for an ETH-native token (e.g., WETH) bridged to StarkNet:
- `unlock_tokens_if_needed(Strk, weth, amount)` → **no-op** (no Strk locked-token entry for ETH-native token)
- `lock_tokens_if_needed(Eth, weth, fee)` → **no-op** (ETH is origin chain) [6](#0-5) 

The transfer is stored as a pending cross-chain message for MPC signing. The MPC network signs a payload to release `amount − fee` tokens on ETH. The ETH bridge releases those tokens from its locked pool — tokens that were deposited by other users bridging ETH→NEAR. [7](#0-6) 

### Impact Explanation

**Theft scenario:** An attacker who controls ETH address `0x<hex40>` calls StarkNet `init_transfer` with `recipient = "<hex40>"` (no prefix). The NEAR bridge routes the finalized transfer to ETH, the MPC signs the release, and the ETH bridge pays out tokens from the pool locked by legitimate ETH→NEAR bridgers. The attacker receives ETH tokens they did not deposit; the original depositors' funds are permanently unclaimable.

**Permanent lock scenario:** If the attacker uses a hex40 string they do not control on ETH, the MPC-signed ETH release goes to an uncontrolled address, permanently stranding the tokens. The StarkNet-side burn is already irreversible and the NEAR nonce is consumed.

In both cases the NEAR bridge's locked-token accounting is corrupted: the ETH pool balance tracked on NEAR diverges from the actual ETH bridge balance.

### Likelihood Explanation

- The StarkNet `init_transfer` is a public, permissionless call.
- Crafting a 40-char hex recipient is trivial — any ETH wallet address (stripped of `0x`) qualifies.
- The attacker only needs wrapped tokens on StarkNet (obtainable by bridging or buying on-chain).
- No privileged role, key leak, or MPC collusion is required; the MPC signs any structurally valid transfer message.

### Recommendation

1. **Reject prefix-less recipients in `parse_init_transfer`**: after `read_byte_array`, assert the string contains `:` before calling `.parse()`, or explicitly require the `near:` prefix for NEAR-destined transfers.
2. **Harden `OmniAddress::from_str`**: remove the `unwrap_or(("eth", input))` default. Require an explicit chain prefix; return an error if none is present.
3. **Add recipient-chain validation in `fin_transfer_callback`**: cross-check that the parsed recipient chain matches the expected destination for the originating chain (e.g., StarkNet→NEAR transfers must have `OmniAddress::Near` recipients).

### Proof of Concept

```rust
// Step 1: confirm "alice.near" fails (non-hex chars)
assert!(OmniAddress::from_str("alice.near").is_err());

// Step 2: confirm a 40-char hex string (valid NEAR account ID) parses as ETH
let hex40 = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef";
// Valid NEAR account ID: only [a-f0-9], length 40 ≤ 64
assert!(near_sdk::AccountId::from_str(hex40).is_ok());
// Parses as OmniAddress::Eth, NOT OmniAddress::Near
let addr = OmniAddress::from_str(hex40).unwrap();
assert_eq!(addr.get_chain(), ChainKind::Eth);  // BUG: should be Near or error

// Step 3: craft StarkNet InitTransfer event with recipient = hex40
// (no "near:" prefix), submit proof to NEAR bridge fin_transfer →
// routes to process_fin_transfer_to_other_chain → MPC signs ETH release →
// attacker receives ETH tokens at 0xdeadbeef...deadbeef
``` [8](#0-7) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-types/src/lib.rs (L392-411)
```rust
    fn from_str(input: &str) -> Result<Self, Self::Err> {
        let (chain, recipient) = input.split_once(':').unwrap_or(("eth", input));

        match chain {
            "eth" => Ok(Self::Eth(recipient.parse().map_err(stringify)?)),
            "near" => Ok(Self::Near(recipient.parse().map_err(stringify)?)),
            "sol" => Ok(Self::Sol(recipient.parse().map_err(stringify)?)),
            "arb" => Ok(Self::Arb(recipient.parse().map_err(stringify)?)),
            "base" => Ok(Self::Base(recipient.parse().map_err(stringify)?)),
            "bnb" => Ok(Self::Bnb(recipient.parse().map_err(stringify)?)),
            "pol" => Ok(Self::Pol(recipient.parse().map_err(stringify)?)),
            "hlevm" => Ok(Self::HyperEvm(recipient.parse().map_err(stringify)?)),
            "abs" => Ok(Self::Abs(recipient.parse().map_err(stringify)?)),
            "btc" => Ok(Self::Btc(recipient.to_string())),
            "zcash" => Ok(Self::Zcash(recipient.to_string())),
            "strk" => Ok(Self::Strk(recipient.parse().map_err(stringify)?)),
            "fogo" => Ok(Self::Fogo(recipient.parse().map_err(stringify)?)),
            _ => Err(format!("Chain {chain} is not supported")),
        }
    }
```

**File:** near/omni-types/src/hex_types.rs (L21-38)
```rust
            fn from_str(s: &str) -> Result<Self, Self::Err> {
                let hex_str = s.strip_prefix("0x").unwrap_or(s);
                if hex_str.len() > $size * 2 {
                    return Err(TypesError::InvalidHexLength);
                }

                let hex_str = if $padded {
                    &format!("{:0>width$}", hex_str, width = $size * 2)
                } else {
                    hex_str
                };

                let result = Vec::from_hex(&hex_str).map_err(|_| TypesError::InvalidHex)?;
                Ok(Self(
                    result
                        .try_into()
                        .map_err(|_| TypesError::InvalidHexLength)?,
                ))
```

**File:** starknet/src/omni_bridge.cairo (L281-330)
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
```

**File:** near/omni-types/src/starknet/events.rs (L57-61)
```rust
    let recipient_str = cursor.read_byte_array()?;
    let msg = cursor.read_byte_array()?;

    let emitter_address = OmniAddress::Strk(H256(*from_address));
    let recipient: OmniAddress = recipient_str.parse().map_err(stringify)?;
```

**File:** near/omni-bridge/src/lib.rs (L734-745)
```rust
        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
        }
```

**File:** near/omni-bridge/src/lib.rs (L1980-2054)
```rust
    fn process_fin_transfer_to_other_chain(
        &mut self,
        predecessor_account_id: AccountId,
        transfer_message: TransferMessage,
    ) {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
        let token = self.get_token_id(&transfer_message.token);

        if transfer_message.recipient.is_utxo_chain() {
            let btc_account_id =
                self.get_utxo_chain_token(transfer_message.get_destination_chain());
            require!(
                token == btc_account_id,
                BridgeError::NativeTokenRequiredForChain.as_ref()
            );
        }

        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );

            None
        };

        // If fast transfer happened, send tokens to the relayer that executed fast transfer
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
        } else {
            required_balance = self
                .add_transfer_message(transfer_message.clone(), predecessor_account_id.clone())
                .saturating_add(required_balance);
        }

        self.update_storage_balance(
            predecessor_account_id,
            required_balance,
            env::attached_deposit(),
        );

        env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L96-120)
```rust
    pub(crate) fn lock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.lock_tokens(chain_kind, token_id, amount)
    }

    pub(crate) fn unlock_tokens_if_needed(
        &mut self,
        chain_kind: ChainKind,
        token_id: &AccountId,
        amount: u128,
    ) -> LockAction {
        if self.get_token_origin_chain(token_id) == chain_kind || amount == 0 {
            return LockAction::Unchanged;
        }

        self.unlock_tokens(chain_kind, token_id, amount)
    }
```
