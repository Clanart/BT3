### Title
Malformed `recipient` ByteArray in StarkNet `init_transfer` permanently locks user tokens — (`near/omni-types/src/starknet/events.rs`)

### Summary

The StarkNet `init_transfer` function accepts any `ByteArray` as `recipient` with no format validation. When the relayer submits the resulting MPC proof, `parse_init_transfer` calls `OmniAddress::from_str` on the raw string and propagates the error with `?`, causing the entire proof verification to fail. Because the StarkNet nonce is already consumed and no on-chain recovery path exists on either side, the user's tokens are permanently locked.

### Finding Description

**StarkNet side — no recipient validation:**

The Cairo `init_transfer` function in `starknet/src/omni_bridge.cairo` only checks `amount > 0` and `fee < amount`. It accepts any `ByteArray` as `recipient`, burns/locks the user's tokens, increments `current_origin_nonce`, and emits the `InitTransfer` event — all before any format check on the recipient string. [1](#0-0) 

**Rust side — hard parse failure:**

`parse_init_transfer` in `near/omni-types/src/starknet/events.rs` reads the recipient as a raw string and immediately calls `OmniAddress::from_str` via `.parse()`, propagating any error with `?`:

```rust
let recipient: OmniAddress = recipient_str.parse().map_err(stringify)?;
``` [2](#0-1) 

`OmniAddress::from_str` rejects any string that does not match a known `chain:address` pattern: [3](#0-2) 

Confirmed failure cases from the test suite:
- `"invalid_format"` → `Err("ERR_INVALID_HEX")` (no `:`, defaults to `eth` prefix, EVM hex parse fails)
- `"unknown:address"` → `Err("Chain unknown is not supported")`
- `"near:invalid account!!"` → NEAR `AccountId` parse fails [4](#0-3) 

**Error propagation to NEAR:**

`parse_starknet_proof` → `parse_init_transfer` returns `Err` → `verify_callback` in the MPC prover returns `Err(String)` → the prover promise result is `Failed` → `decode_prover_result(0)` returns `Err(PromiseError::Failed)` → `fin_transfer_callback` panics with `BridgeError::InvalidProofMessage`: [5](#0-4) [6](#0-5) [7](#0-6) 

**No recovery path:**

There is no `cancel_transfer`, refund, or retry mechanism on the StarkNet contract for a consumed `origin_nonce`. The NEAR bridge never records the transfer as finalized, so the nonce cannot be replayed. The user's tokens are irrecoverably locked.

### Impact Explanation

**Critical — permanent, irrecoverable lock of user funds on StarkNet.**

Any user who calls StarkNet `init_transfer` with a recipient string that the Cairo contract accepts but `OmniAddress::from_str` rejects will have their tokens permanently locked. The StarkNet nonce is consumed, the tokens are burned or held by the bridge contract, and no settlement on NEAR is ever possible.

### Likelihood Explanation

**High.** The attack requires no privilege — any user can call `init_transfer` on StarkNet. The Cairo contract imposes zero format constraints on the `recipient` field. A user who mistypes a chain prefix (e.g., `"ethereum:0x..."` instead of `"eth:0x..."`), uses an unsupported chain, or passes any non-conforming string triggers the lock. This can also happen accidentally, not just maliciously.

### Recommendation

Validate the `recipient` ByteArray format on the StarkNet side before accepting the transfer. The Cairo contract should enforce that the recipient string matches the expected `chain:address` format (e.g., via a prefix allowlist or a length/character check). Alternatively, add a NEAR-side recovery mechanism that allows an admin or the original sender to reclaim funds for a StarkNet nonce whose proof parsing permanently fails.

### Proof of Concept

1. User calls `starknet::omni_bridge.init_transfer(token, amount=100, fee=1, native_fee=0, recipient="xyz:badaddr", message="")`.
2. Cairo accepts: `amount > 0` ✓, `fee < amount` ✓. Tokens burned. `origin_nonce = N` consumed. `InitTransfer` event emitted.
3. Relayer submits MPC proof for `ProofKind::InitTransfer`.
4. `verify_callback` → `parse_starknet_result` → `parse_starknet_proof` → `parse_init_transfer`:
   - `recipient_str = "xyz:badaddr"`
   - `"xyz:badaddr".parse::<OmniAddress>()` → `Err("Chain xyz is not supported")`
   - `?` propagates → `parse_init_transfer` returns `Err`
5. `verify_callback` returns `Err(String)` → prover promise is `Failed`.
6. `fin_transfer_callback`: `decode_prover_result(0)` → `Err(PromiseError::Failed)` → `env::panic_str("ERR_INVALID_PROOF_MESSAGE")`.
7. No NEAR-side state is written. StarkNet nonce `N` is consumed with no recovery. User tokens are permanently locked.

### Citations

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

**File:** near/omni-types/src/tests/lib_test.rs (L272-281)
```rust
        (
            "invalid_format".to_string(),
            Err("ERR_INVALID_HEX".to_string()),
            "Should fail on missing chain prefix",
        ),
        (
            "unknown:address".to_string(),
            Err("Chain unknown is not supported".to_string()),
            "Should fail on unsupported chain",
        ),
```

**File:** near/omni-bridge/src/lib.rs (L704-707)
```rust
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
```

**File:** near/omni-bridge/src/lib.rs (L2159-2166)
```rust
    fn decode_prover_result(result_idx: u64) -> Result<ProverResult, PromiseError> {
        match env::promise_result_checked(result_idx, usize::MAX) {
            Ok(data) => {
                Ok(ProverResult::try_from_slice(&data).near_expect(BridgeError::InvalidProof))
            }
            Err(_) => Err(PromiseError::Failed),
        }
    }
```

**File:** near/omni-prover/mpc-omni-prover/src/lib.rs (L190-209)
```rust
    fn parse_starknet_result(
        kind: ProofKind,
        chain_kind: ChainKind,
        payload: &ForeignTxSignPayloadV1,
    ) -> Result<ProverResult, String> {
        if payload.values.len() != 1 {
            return Err(ProverError::InvalidPayloadValuesLength.to_string());
        }

        let Some(ExtractedValue::StarknetExtractedValue(StarknetExtractedValue::Log(starknet_log))) =
            payload.values.first()
        else {
            return Err(ProverError::InvalidProof.to_string());
        };

        let keys: Vec<[u8; 32]> = starknet_log.keys.iter().map(|k| k.0).collect();
        let data: Vec<[u8; 32]> = starknet_log.data.iter().map(|d| d.0).collect();

        parse_starknet_proof(kind, chain_kind, &starknet_log.from_address.0, &keys, &data)
    }
```
