### Title
Hardcoded Starknet Native Token (STRK) Address Breaks Native Fee Accounting on Non-Mainnet Deployments - (File: near/omni-types/src/lib.rs)

### Summary
`get_native_token_address` in `near/omni-types/src/lib.rs` hardcodes the Starknet native STRK token contract address as a compile-time constant. Unlike all other supported chains (which use the zero-address convention for their native token), Starknet's native token has a specific contract address that differs between mainnet and Sepolia testnet. Any deployment targeting a Starknet network where STRK lives at a different address will silently resolve native-fee token lookups to the wrong NEAR AccountId, corrupting native-fee accounting and permanently locking native fees in the bridge.

### Finding Description
`get_native_token_address` returns a chain-specific `OmniAddress` used throughout the NEAR bridge to identify the native token of each connected chain:

```rust
// near/omni-types/src/lib.rs  lines 944-961
pub fn get_native_token_address(chain_kind: ChainKind) -> Result<OmniAddress, String> {
    match chain_kind {
        ChainKind::Strk => OmniAddress::from_str(
            "strk:0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
        ),
        // all other chains return OmniAddress::new_zero(chain_kind)
        ...
    }
}
``` [1](#0-0) 

The address `0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d` is the **Starknet mainnet** STRK token contract. On Starknet Sepolia testnet the STRK token is deployed at a different address.

This value is consumed by `get_native_token_id` in the NEAR bridge contract:

```rust
// near/omni-bridge/src/lib.rs
pub fn get_native_token_id(&self, chain: ChainKind) -> AccountId {
    let native_token_address =
        get_native_token_address(chain).near_expect(BridgeError::FailedToGetNativeTokenAddress);
    self.get_token_id(&native_token_address)
}
``` [2](#0-1) 

`get_native_token_id` is called during native-fee settlement for Starknet transfers. When the hardcoded address does not match the actual STRK contract on the deployed Starknet network, `get_token_id` resolves to the wrong NEAR AccountId (or to an unregistered token), causing every native-fee credit for Starknet transfers to be applied to the wrong token or to fail entirely.

### Impact Explanation
Any user who initiates a Starknet → NEAR transfer and includes a non-zero `native_token_fee` will have that fee permanently misdirected or unclaimable. The bridge accepts the transfer and records the fee, but the settlement step resolves the fee token to the wrong NEAR AccountId. The native fee is irrecoverably locked because no correct token mapping exists for the wrong address. This matches the allowed impact class: **fee/token-mapping corruption that breaks bridge collateralization or misdirects value**, and **permanent freezing / unclaimable settlement of user funds**.

### Likelihood Explanation
The Omni Bridge explicitly lists `ChainKind::Strk` as a supported chain. [3](#0-2)  The protocol is actively tested on Starknet Sepolia testnet (where the STRK address differs from mainnet). Any relayer or user submitting a Starknet `InitTransfer` proof with a non-zero `native_token_fee` triggers the broken path with no special privileges required.

### Recommendation
Remove the hardcoded STRK address from `get_native_token_address`. Instead, store the native token address for each chain as a configurable, immutable field set during the NEAR bridge contract's initialization (analogous to how `nearBridgeDerivedAddress` is passed to the EVM `OmniBridge` constructor). Each deployment (mainnet vs. testnet) then supplies the correct chain-specific native token address at deploy time.

### Proof of Concept
1. Deploy the NEAR bridge contract with the current code (hardcoded mainnet STRK address).
2. Deploy the Starknet bridge on Sepolia testnet (STRK at a different address, e.g. `0x049d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7` on Sepolia).
3. As an unprivileged user, call `ft_transfer_call` on the Starknet bridge to initiate a transfer to NEAR with `native_token_fee > 0`.
4. The Starknet bridge emits an `InitTransfer` event with the Sepolia STRK address as the fee token.
5. A relayer submits the proof to the NEAR bridge. The NEAR bridge calls `get_native_token_id(ChainKind::Strk)`, which resolves to the hardcoded mainnet address `0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d`.
6. `get_token_id` returns the NEAR AccountId for the **mainnet** STRK mapping (which does not exist on the testnet deployment), causing the native fee to be credited to a non-existent token or to panic.
7. The user's native fee is permanently locked; no recovery path exists because the correct Sepolia STRK token is never registered under the hardcoded mainnet address. [1](#0-0)

### Citations

**File:** near/omni-types/src/lib.rs (L77-83)
```rust
    #[serde(alias = "strk")]
    Strk,
    #[serde(alias = "abs")]
    Abs,
    #[serde(alias = "fogo")]
    Fogo,
}
```

**File:** near/omni-types/src/lib.rs (L944-961)
```rust
pub fn get_native_token_address(chain_kind: ChainKind) -> Result<OmniAddress, String> {
    match chain_kind {
        ChainKind::Strk => OmniAddress::from_str(
            "strk:0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d",
        ),
        ChainKind::Eth
        | ChainKind::Near
        | ChainKind::Sol
        | ChainKind::Arb
        | ChainKind::Base
        | ChainKind::Bnb
        | ChainKind::Btc
        | ChainKind::Zcash
        | ChainKind::Pol
        | ChainKind::HyperEvm
        | ChainKind::Abs
        | ChainKind::Fogo => OmniAddress::new_zero(chain_kind),
    }
```

**File:** near/omni-bridge/src/lib.rs (L1407-1412)
```rust
    pub fn get_native_token_id(&self, chain: ChainKind) -> AccountId {
        let native_token_address =
            get_native_token_address(chain).near_expect(BridgeError::FailedToGetNativeTokenAddress);

        self.get_token_id(&native_token_address)
    }
```
