### Title
Hardcoded Mainnet Fee Token Addresses in `ExecutionConfig::default()` Cause Wrong `virtual_os_config_hash` in RPC Simulation/Fee Estimation - (`File: crates/apollo_rpc_execution/src/lib.rs`)

### Summary

`ExecutionConfig::default()` in `crates/apollo_rpc_execution/src/lib.rs` hardcodes Ethereum mainnet STRK and ETH fee token contract addresses. These addresses flow directly into the `ChainInfo` used by `create_block_context()`, which is then consumed by `validate_proof_facts()` to compute `virtual_os_config_hash`. On any non-mainnet deployment (e.g., Sepolia) where the config is not explicitly overridden, the RPC simulation, fee estimation, and tracing paths compute a wrong `virtual_os_config_hash`, causing authoritative-looking wrong results for any Invoke V3 transaction carrying SNOS proof facts.

### Finding Description

In `crates/apollo_rpc_execution/src/lib.rs` lines 94–141, two mainnet-specific addresses are hardcoded as module-level constants and used as the `Default` for `ExecutionConfig`:

```rust
const STRK_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d";
const ETH_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7";

impl Default for ExecutionConfig {
    fn default() -> Self {
        ExecutionConfig {
            strk_fee_contract_address: *STRK_FEE_CONTRACT_ADDRESS,
            eth_fee_contract_address:  *ETH_FEE_CONTRACT_ADDRESS,
            ...
        }
    }
}
``` [1](#0-0) 

`create_block_context()` (lines 322–425) directly copies these addresses into `ChainInfo.fee_token_addresses`:

```rust
let chain_info = ChainInfo {
    chain_id,
    fee_token_addresses: FeeTokenAddresses {
        strk_fee_token_address: execution_config.strk_fee_contract_address,
        eth_fee_token_address:  execution_config.eth_fee_contract_address,
    },
    ...
};
``` [2](#0-1) 

The resulting `BlockContext` is passed to the blockifier for full transaction execution (including simulation). Inside `validate_proof_facts()` in `crates/blockifier/src/transaction/account_transaction.rs`, the `strk_fee_token_address` from `ChainInfo` is extracted via `OsChainInfo::from(chain_info)` and hashed into `virtual_os_config_hash`:

```rust
let virtual_os_config_hash = OsChainInfo::from(chain_info)
    .compute_virtual_os_config_hash()
    .expect("Failed to compute OS config hash");
if virtual_os_config_hash != proof_config_hash {
    return Err(TransactionPreValidationError::InvalidProofFacts(...));
}
``` [3](#0-2) 

`compute_virtual_os_config_hash()` is a Pedersen hash over `[STARKNET_OS_CONFIG_HASH_VERSION, chain_id, strk_fee_token_address]`: [4](#0-3) 

On Sepolia, the actual `strk_fee_token_address` deployed at genesis differs from the mainnet constant. The batcher's `ChainInfo` is correctly configured via `batcher_config.static_config.block_builder_config.chain_info.fee_token_addresses` (a separate config pointer), but the RPC execution path uses `ExecutionConfig` which defaults to the hardcoded mainnet value. The two paths therefore compute different `virtual_os_config_hash` values for the same transaction.

### Impact Explanation

Any Invoke V3 transaction that carries SNOS proof facts and is submitted to the RPC for simulation, fee estimation, or tracing on a non-mainnet deployment will have its `virtual_os_config_hash` validated against the wrong (mainnet) fee token address. The RPC will:

- **Reject** valid Sepolia proof-facts transactions as `InvalidProofFacts` during `starknet_simulateTransactions` / `starknet_estimateFee` / `starknet_traceTransaction`, returning an authoritative-looking error that does not reflect actual sequencer behavior.
- **Accept** (during simulation) transactions whose `config_hash` was crafted against the mainnet fee token address, which the actual sequencer (using the correct Sepolia address) would reject.

This matches: **High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

The `ExecutionConfig` struct is serializable and the fields are exposed in the RPC config file (`apollo_rpc/resources/test_config.json`), but the `Default` implementation silently falls back to mainnet addresses if the operator does not explicitly set them. The batcher's fee token addresses are wired through a separate config pointer (`eth_fee_token_address` / `strk_fee_token_address` pointer targets in `config_schema.json`), so a Sepolia deployment that correctly configures the batcher but leaves the RPC `ExecutionConfig` at its default will exhibit this divergence. The trigger requires only an unprivileged user submitting an Invoke V3 transaction with non-empty `proof_facts`. [5](#0-4) 

### Recommendation

Remove the hardcoded mainnet constants from `ExecutionConfig::default()`. Instead, require the fee token addresses to be supplied at construction time, sourced from the same shared config pointer (`eth_fee_token_address` / `strk_fee_token_address`) that the batcher already uses. The `Default` impl should either be removed or panic with an explicit message directing operators to configure the addresses, mirroring the pattern used in `blockifier_reexecution/src/utils.rs` where `get_fee_token_addresses()` explicitly matches known chain IDs and panics on unknown ones. [6](#0-5) 

### Proof of Concept

1. Deploy the Apollo sequencer node targeting Sepolia (`chain_id = "SN_SEPOLIA"`).
2. Leave `ExecutionConfig` at its default (mainnet addresses).
3. Construct an Invoke V3 transaction with `proof_facts` whose `config_hash` = `Pedersen(STARKNET_OS_CONFIG_HASH_VERSION, SN_SEPOLIA_chain_id_felt, sepolia_strk_fee_token_address)` — the value the actual sequencer will accept.
4. Submit to `starknet_simulateTransactions`.
5. The RPC computes `virtual_os_config_hash` = `Pedersen(STARKNET_OS_CONFIG_HASH_VERSION, SN_SEPOLIA_chain_id_felt, mainnet_strk_fee_token_address)` — a different value.
6. `validate_proof_facts` returns `InvalidProofFacts`, and the simulation reports the transaction as invalid, contradicting what the sequencer would actually do.

### Citations

**File:** crates/apollo_rpc_execution/src/lib.rs (L94-141)
```rust
/// The address of the STRK fee contract on Starknet.
const STRK_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x04718f5a0fc34cc1af16a1cdee98ffb20c31f5cd61d6ab07201858f4287c938d";
/// The address of the ETH fee contract on Starknet.
const ETH_FEE_CONTRACT_ADDRESS_STR: &str =
    "0x49d36570d4e46f48e99674bd3fcc84644ddd6b96f7c741b1562b82f9e004dc7";
const DEFAULT_INITIAL_GAS_COST: u64 = 10000000000;

/// Result type for execution functions.
pub type ExecutionResult<T> = Result<T, ExecutionError>;

/// The address of the STRK fee contract on Starknet.
pub static STRK_FEE_CONTRACT_ADDRESS: LazyLock<ContractAddress> = LazyLock::new(|| {
    ContractAddress::try_from(
        Felt::from_hex(STRK_FEE_CONTRACT_ADDRESS_STR)
            .expect("Error converting strk fee contract address from hex"),
    )
    .expect("Error converting strk fee contract address from felt")
});

/// The address of the ETH fee contract on Starknet.
pub static ETH_FEE_CONTRACT_ADDRESS: LazyLock<ContractAddress> = LazyLock::new(|| {
    ContractAddress::try_from(
        Felt::from_hex(ETH_FEE_CONTRACT_ADDRESS_STR)
            .expect("Error converting eth fee contract address from hex"),
    )
    .expect("Error converting eth fee contract address from felt")
});

#[derive(Copy, Clone, Serialize, Deserialize, Debug, PartialEq)]
/// Parameters that are needed for execution.
pub struct ExecutionConfig {
    /// The strk address to receive fees
    pub strk_fee_contract_address: ContractAddress,
    /// The eth address to receive fees
    pub eth_fee_contract_address: ContractAddress,
    /// The initial gas cost for a transaction
    pub default_initial_gas_cost: u64,
}

impl Default for ExecutionConfig {
    fn default() -> Self {
        ExecutionConfig {
            strk_fee_contract_address: *STRK_FEE_CONTRACT_ADDRESS,
            eth_fee_contract_address: *ETH_FEE_CONTRACT_ADDRESS,
            default_initial_gas_cost: DEFAULT_INITIAL_GAS_COST,
        }
    }
```

**File:** crates/apollo_rpc_execution/src/lib.rs (L400-407)
```rust
    let chain_info = ChainInfo {
        chain_id,
        fee_token_addresses: FeeTokenAddresses {
            strk_fee_token_address: execution_config.strk_fee_contract_address,
            eth_fee_token_address: execution_config.eth_fee_contract_address,
        },
        is_l3: false,
    };
```

**File:** crates/blockifier/src/transaction/account_transaction.rs (L333-344)
```rust
        let chain_info = &block_context.chain_info;
        // TODO(Meshi): Cache this computation as part of the chain context.
        let virtual_os_config_hash = OsChainInfo::from(chain_info)
            .compute_virtual_os_config_hash()
            .expect("Failed to compute OS config hash");
        let proof_config_hash = snos_proof_facts.config_hash;
        if virtual_os_config_hash != proof_config_hash {
            return Err(TransactionPreValidationError::InvalidProofFacts(format!(
                "Virtual OS config hash mismatch. Computed virtual OS config hash: \
                 {virtual_os_config_hash}, expected virtual OS config hash: {proof_config_hash}."
            )));
        }
```

**File:** crates/starknet_api/src/core.rs (L135-151)
```rust
    pub fn compute_os_config_hash(
        &self,
        public_keys: Option<&Vec<Felt>>,
    ) -> Result<Felt, StarknetApiError> {
        let mut data = vec![
            STARKNET_OS_CONFIG_HASH_VERSION,
            (&self.chain_id).try_into().map_err(|_| StarknetApiError::OutOfRange {
                string: format!("Invalid chain ID (cannot convert to Felt): {:?}", self.chain_id),
            })?,
            self.strk_fee_token_address.into(),
        ];
        let public_keys_hash = compute_public_keys_hash(public_keys);
        if public_keys_hash != DEFAULT_PUBLIC_KEYS_HASH {
            data.push(public_keys_hash);
        }
        Ok(Pedersen::hash_array(&data))
    }
```

**File:** crates/apollo_node/resources/config_schema.json (L151-161)
```json
  },
  "batcher_config.static_config.block_builder_config.chain_info.fee_token_addresses.eth_fee_token_address": {
    "description": "Address of the ETH fee token.",
    "pointer_target": "eth_fee_token_address",
    "privacy": "Public"
  },
  "batcher_config.static_config.block_builder_config.chain_info.fee_token_addresses.strk_fee_token_address": {
    "description": "Address of the STRK fee token.",
    "pointer_target": "strk_fee_token_address",
    "privacy": "Public"
  },
```

**File:** crates/blockifier_reexecution/src/utils.rs (L61-73)
```rust
pub fn get_fee_token_addresses(
    chain_id: &ChainId,
    strk_fee_token_address_override: Option<ContractAddress>,
) -> FeeTokenAddresses {
    match chain_id {
        // Mainnet, testnet and integration systems have the same fee token addresses.
        ChainId::Mainnet | ChainId::Sepolia | ChainId::IntegrationSepolia => FeeTokenAddresses {
            strk_fee_token_address: strk_fee_token_address_override
                .unwrap_or(*STRK_FEE_CONTRACT_ADDRESS),
            eth_fee_token_address: *ETH_FEE_CONTRACT_ADDRESS,
        },
        unknown_chain => unimplemented!("Unknown chain ID {unknown_chain}."),
    }
```
