The exploit path is concrete. Let me trace it precisely through the code.

**Execution trace:**

In `process()`, when `token_program == token_2022::ID`:
- Reads `MetadataPointer` from `M_attacker`'s mint data
- `metadata_pointer.metadata_address.0` = USDC's Metaplex PDA ≠ `M_attacker` → not embedded
- ≠ `Pubkey::default()` → enters "Third-party metadata" branch at line 104
- Calls `parse_metadata_account(usdc_metaplex_pda)`

In `parse_metadata_account()`:
- `require_keys_eq!(metadata.key(), address)` → `usdc_metaplex_pda == usdc_metaplex_pda` → **passes** (attacker supplied this account)
- `metadata.owner == &MetaplexID` → USDC's Metaplex account is owned by MetaplexID → **passes**
- Deserializes → returns `("USD Coin", "USDC")`
- **Never checks `MplMetadata.mint == self.mint.key()`**

The `LogMetadataPayload` is then built with `token: M_attacker` but `name: "USD Coin"`, `symbol: "USDC"`.

On NEAR, `deploy_token_internal` keys the wrapped token by `token_address` (M_attacker's pubkey), not by name/symbol — so no collision check prevents this deployment.

---

### Title
Metadata Pointer Spoofing in Token-2022 `log_metadata` Allows Arbitrary Name/Symbol Injection for Any Mint — (`solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs`)

### Summary
`parse_metadata_account` validates that the supplied metadata account's address matches the mint's `metadata_pointer.metadata_address` and that the account is owned by MetaplexID, but **never verifies that the deserialized `MplMetadata.mint` field equals the mint being registered**. An attacker who controls a Token-2022 mint can set its `metadata_pointer` to point to any legitimate Metaplex metadata account (e.g., USDC's), pass that account as the `metadata` argument, and cause the bridge to emit a Wormhole message associating the attacker's mint address with the victim token's name and symbol.

### Finding Description

The vulnerable logic is in `parse_metadata_account`: [1](#0-0) 

The function performs two checks:
1. The passed `metadata` account key equals the address read from the mint's `MetadataPointer` extension.
2. The account is owned by `MetaplexID`.

Neither check binds the metadata account to the mint being registered. The Metaplex `MetadataAccount` struct contains a `mint` field that identifies which mint the metadata belongs to, but it is never read or compared against `self.mint.key()`.

The "Third-party metadata" branch that calls `parse_metadata_account` is reached when `metadata_pointer.metadata_address.0 != self.mint.key()` and `!= Pubkey::default()`: [2](#0-1) 

An attacker can freely set their Token-2022 mint's `metadata_pointer.metadata_address` to any arbitrary pubkey — including the canonical Metaplex PDA of USDC — because the Token-2022 `MetadataPointer` extension places no restriction on what address the pointer targets.

The resulting payload: [3](#0-2) 

carries `token: M_attacker` with `name: "USD Coin"` and `symbol: "USDC"`, which is then posted via Wormhole.

On NEAR, `deploy_token_callback` passes this directly to `deploy_token_internal` without any name/symbol uniqueness check: [4](#0-3) 

`deploy_token_internal` keys the wrapped token by the Solana address prefix of `M_attacker`, not by name/symbol: [5](#0-4) 

So a wrapped token named "USD Coin (USDC)" is deployed on NEAR for `M_attacker` with no collision against the real USDC registration.

### Impact Explanation

- A wrapped token with USDC's exact name and symbol is deployed on NEAR, permanently associated with the attacker's mint address.
- Any user who sees this token on NEAR and bridges it back to Solana receives `M_attacker` tokens (worthless), not real USDC.
- The real USDC (`M_usdc`) can still be registered separately (keyed by its own address), so the canonical USDC registration is not permanently blocked — but two "USD Coin / USDC" tokens now coexist on NEAR, creating a persistent identity-spoofing surface that misdirects user value.
- This fits the **High** impact category: token-mapping corruption that misdirects value across the bridge.

### Likelihood Explanation

- Requires no privileged access. Any account can create a Token-2022 mint and set its `MetadataPointer` to any address.
- USDC's Metaplex metadata account is public and permanently on-chain.
- The attack requires only a single `log_metadata` call followed by a `deploy_token` proof submission on NEAR.
- Likelihood is **High**.

### Recommendation

After deserializing the Metaplex metadata in `parse_metadata_account`, add a check that the metadata's `mint` field matches the mint being registered:

```rust
if metadata.owner == &MetaplexID {
    let data = metadata.try_borrow_data()?;
    let mpl_metadata = MplMetadata::try_deserialize(&mut data.as_ref())?;
    // ADD THIS CHECK:
    require_keys_eq!(
        mpl_metadata.mint,
        self.mint.key(),
        ErrorCode::InvalidTokenMetadataAddress,
    );
    Ok((mpl_metadata.name.clone(), mpl_metadata.symbol.clone()))
}
```

This ensures the metadata account is actually the canonical metadata for the mint being registered, not an arbitrary third-party account.

### Proof of Concept

```rust
// 1. Attacker creates Token-2022 mint M_attacker with MetadataPointer
//    pointing to USDC's Metaplex PDA
let usdc_metaplex_pda = Pubkey::find_program_address(
    &[b"metadata", MetaplexID.as_ref(), &usdc_mint.to_bytes()],
    &MetaplexID,
).0;
// set M_attacker.metadata_pointer.metadata_address = usdc_metaplex_pda

// 2. Call log_metadata
log_metadata(
    mint = M_attacker,
    metadata = Some(usdc_metaplex_pda_account),  // real USDC Metaplex account
    token_program = Token2022,
);

// 3. Resulting Wormhole payload:
// LogMetadataPayload { token: M_attacker, name: "USD Coin", symbol: "USDC", decimals: 6 }

// 4. Differential assertion:
assert_eq!(payload_attacker.name, payload_usdc.name);    // both "USD Coin"
assert_eq!(payload_attacker.symbol, payload_usdc.symbol); // both "USDC"
assert_ne!(payload_attacker.token, payload_usdc.token);   // different mint addresses
```

### Citations

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L72-90)
```rust
    fn parse_metadata_account(&self, address: Pubkey) -> Result<(String, String)> {
        let metadata = self
            .metadata
            .as_ref()
            .ok_or_else(|| error!(ErrorCode::TokenMetadataNotProvided))?
            .to_account_info();
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
    }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/log_metadata.rs (L104-106)
```rust
                } else if metadata_pointer.metadata_address.0 != Pubkey::default() {
                    // Third-party metadata
                    self.parse_metadata_account(metadata_pointer.metadata_address.0)?
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

**File:** near/omni-bridge/src/lib.rs (L1165-1174)
```rust
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
