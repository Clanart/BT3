### Title
Token-2022 Metadata Pointer Spoofing Allows Arbitrary Name/Symbol in LogMetadata Wormhole Message — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary

`LogMetadata::process` / `parse_metadata_account` does not verify that a Token-2022 mint's `metadata_pointer.metadata_address` is the canonical Metaplex PDA derived from that mint. An unprivileged attacker can create a Token-2022 mint whose `metadata_pointer` points to the Metaplex metadata account of any high-value token (e.g., USDC), pass that Metaplex account as the optional `metadata` argument, and cause the bridge to emit a Wormhole `LogMetadata` message associating the attacker's mint address with the victim token's name and symbol. NEAR then deploys a wrapped token for the attacker's mint under the victim's identity.

---

### Finding Description

For classic SPL tokens, `process()` derives the canonical Metaplex PDA deterministically from the mint being registered: [1](#0-0) 

For Token-2022 mints, when `metadata_pointer.metadata_address` is neither the mint itself nor `Pubkey::default()`, the code takes the "third-party metadata" branch and calls `parse_metadata_account` with whatever address the attacker wrote into the `metadata_pointer` extension: [2](#0-1) 

`parse_metadata_account` then performs only two checks:

1. The passed `metadata` account's key equals `metadata_pointer.metadata_address` (trivially satisfied — the attacker passes the same account they wrote into the pointer).
2. The account is owned by `MetaplexID`. [3](#0-2) 

There is no check that `metadata_pointer.metadata_address` is the PDA derived from `[METADATA_SEED, MetaplexID, mint.key()]` for the mint being registered. Because any Token-2022 mint authority can freely set `metadata_pointer.metadata_address` to any arbitrary public key at mint creation time, the attacker can point it to the Metaplex metadata PDA of USDC (or any other token), pass that PDA as the `metadata` optional account, and both checks pass.

The resulting `LogMetadataPayload` carries `token = M_attacker` but `name = "USD Coin"` and `symbol = "USDC"`: [4](#0-3) 

On NEAR, `deploy_token_callback` trusts the Wormhole-verified payload and calls `deploy_token_internal` with the spoofed metadata: [5](#0-4) 

`deploy_token_internal` derives the NEAR token account ID from `M_attacker`'s address hash and deploys a NEP-141 token with name "USD Coin" / symbol "USDC": [6](#0-5) 

The `add_token` call inserts `Sol(M_attacker) → fake_usdc_near_id` into `token_address_to_id` and `token_id_to_address`: [7](#0-6) 

---

### Impact Explanation

A permanently deployed NEP-141 token with USDC's name and symbol exists on NEAR, mapped to the attacker's Solana mint. Any user who bridges assets to this token receives tokens backed by `M_attacker`, not real USDC. The token-address mapping is permanently corrupted for `Sol(M_attacker)`. The legitimate USDC mint (`M_usdc`) can still register its own wrapped token (different address key), so the claim of "blocking USDC registration" is incorrect — but the spoofed token persists and can misdirect value through user confusion or downstream integrations that index by name/symbol.

---

### Likelihood Explanation

The preconditions are minimal: create a Token-2022 mint (permissionless on Solana), set `metadata_pointer` to any existing Metaplex PDA, call `log_metadata`. No privileged access, no key compromise, no colluding validators required. The USDC Metaplex metadata account is public and permanently on-chain.

---

### Recommendation

In the Token-2022 "third-party metadata" branch, verify that `metadata_pointer.metadata_address` equals the canonical Metaplex PDA derived from the mint being registered, exactly as is done for classic SPL tokens:

```rust
let expected_pda = Pubkey::find_program_address(
    &[METADATA_SEED, MetaplexID.as_ref(), &self.mint.key().to_bytes()],
    &MetaplexID,
).0;
require_keys_eq!(
    metadata_pointer.metadata_address.0,
    expected_pda,
    ErrorCode::InvalidTokenMetadataAddress,
);
```

This ensures that even for Token-2022 mints, the only accepted external metadata account is the one canonically derived from the mint's own address.

---

### Proof of Concept

```
1. attacker_mint = Token-2022 mint created by attacker
   - metadata_pointer.metadata_address = Metaplex PDA of USDC mint

2. usdc_metaplex_pda = find_program_address(
       [b"metadata", MetaplexID, usdc_mint.key()], MetaplexID)

3. Call: log_metadata(
       mint = attacker_mint,
       metadata = Some(usdc_metaplex_pda),   // UncheckedAccount, no constraint
       token_program = Token-2022)

4. process():
   - metadata_pointer.metadata_address.0 != attacker_mint.key()  → third-party branch
   - parse_metadata_account(usdc_metaplex_pda):
       require_keys_eq!(usdc_metaplex_pda, usdc_metaplex_pda) ✓
       usdc_metaplex_pda.owner == MetaplexID ✓
       returns ("USD Coin", "USDC")

5. Wormhole message posted:
   LogMetadataPayload { token: attacker_mint, name: "USD Coin", symbol: "USDC", decimals: X }

6. NEAR deploy_token_callback:
   token_address = Sol(attacker_mint)
   deploy_token_internal → NEP-141 "USD Coin"/"USDC" deployed, mapped to Sol(attacker_mint)

7. Differential assertion:
   assert payload_attacker.token   != payload_usdc.token    // different mints
   assert payload_attacker.name    == payload_usdc.name     // both "USD Coin"  ← BUG
   assert payload_attacker.symbol  == payload_usdc.symbol   // both "USDC"      ← BUG
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L78-89)
```rust
        require_keys_eq!(
            metadata.key(),
            address,
            ErrorCode::InvalidTokenMetadataAddress,
        );
        if metadata.owner == &MetaplexID {
            let data = metadata.try_borrow_data()?;
            let metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
            Ok((metadata.name.clone(), metadata.symbol.clone()))
        } else {
            Ok((String::default(), String::default()))
        }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L104-106)
```rust
                } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
                    // Third-party metadata
                    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L117-127)
```rust
            self.parse_metadata_account(
                Pubkey::find_program_address(
                    &[
                        METADATA_SEED,
                        MetaplexID.as_ref(),
                        &self.mint.key().to_bytes(),
                    ],
                    &MetaplexID,
                )
                .0,
            )?
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L130-136)
```rust
        let payload = LogMetadataPayload {
            token: self.mint.key(),
            name: name.trim_end_matches('\0').to_string(),
            symbol: symbol.trim_end_matches('\0').to_string(),
            decimals: self.mint.decimals,
        }
        .serialize_for_near(())?;
```

**File:** near/omni-bridge/src/lib.rs (L1155-1174)
```rust
        let Ok(ProverResult::LogMetadata(metadata)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str());
        };

        let chain = metadata.emitter_address.get_chain();
        require!(
            self.factories.get(&chain) == Some(metadata.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        self.deploy_token_internal(
            chain,
            &metadata.token_address,
            BasicMetadata {
                name: metadata.name,
                symbol: metadata.symbol,
                decimals: metadata.decimals,
            },
            attached_deposit,
        )
```

**File:** near/omni-bridge/src/lib.rs (L2408-2426)
```rust
        let prefix = token_address.get_token_prefix();
        let token_id: AccountId = format!("{prefix}.{deployer}")
            .parse()
            .unwrap_or_else(|_| env::panic_str(BridgeError::ParseAccountId.to_string().as_str()));

        let storage_usage = env::storage_usage();
        self.add_token(
            &token_id,
            token_address,
            metadata.decimals,
            metadata.decimals,
        );

        require!(
            self.deployed_tokens.insert(&token_id),
            BridgeError::TokenExists.as_ref()
        );
        self.deployed_tokens_v2
            .insert(&token_id, &token_address.get_chain());
```

**File:** near/omni-bridge/src/lib.rs (L2704-2735)
```rust
    fn add_token(
        &mut self,
        token_id: &AccountId,
        token_address: &OmniAddress,
        decimals: u8,
        origin_decimals: u8,
    ) {
        let chain_kind = token_address.get_chain();
        require!(
            self.token_id_to_address
                .insert(&(chain_kind, token_id.clone()), token_address)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
        require!(
            self.token_address_to_id
                .insert(token_address, token_id)
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
```
