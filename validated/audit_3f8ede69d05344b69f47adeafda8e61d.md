[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L21-24)
```text
/// Note that removing a feature flag still requires the function which tests for the feature
/// (like `code_dependency_check_enabled` below) to stay around for compatibility reasons, as it
/// is a public function. However, once the feature flag is disabled, those functions can constantly
/// return true.
```

**File:** aptos-move/framework/move-stdlib/sources/configs/features.move (L700-900)
```text
    ///
    /// We do not expect use from Move, so for now only for documentation purposes here
    const VM_BINARY_FORMAT_V8: u64 = 86;

    /// Whether the batch Bulletproofs native functions are available. This is needed because of the introduction of a new native function.
    /// Lifetime: transient
    const BULLETPROOFS_BATCH_NATIVES: u64 = 87;

    public fun get_bulletproofs_batch_feature(): u64 {
        BULLETPROOFS_BATCH_NATIVES
    }

    public fun bulletproofs_batch_enabled(): bool {
        is_enabled(BULLETPROOFS_BATCH_NATIVES)
    }

    /// Whether the account abstraction is enabled.
    ///
    /// Lifetime: transient
    const DERIVABLE_ACCOUNT_ABSTRACTION: u64 = 88;

    public fun is_derivable_account_abstraction_enabled(): bool {
        is_enabled(DERIVABLE_ACCOUNT_ABSTRACTION)
    }

    #[deprecated]
    public fun is_domain_account_abstraction_enabled(): bool {
        false
    }

    /// Whether new accounts default to the Fungible Asset store.
    /// Lifetime: transient
    const NEW_ACCOUNTS_DEFAULT_TO_FA_STORE: u64 = 90;

    #[deprecated]
    public fun get_new_accounts_default_to_fa_store_feature(): u64 {
        abort error::invalid_argument(EINVALID_FEATURE)
    }

    #[deprecated]
    public fun new_accounts_default_to_fa_store_enabled(): bool {
        true
    }

    /// Lifetime: transient
    const DEFAULT_ACCOUNT_RESOURCE: u64 = 91;

    public fun get_default_account_resource_feature(): u64 {
        DEFAULT_ACCOUNT_RESOURCE
    }

    public fun is_default_account_resource_enabled(): bool {
        is_enabled(DEFAULT_ACCOUNT_RESOURCE)
    }

    /// If enabled, JWK consensus should run in per-key mode, where:
    /// - The consensus is for key-level updates
    ///   (e.g., "issuer A key 1 should be deleted", "issuer B key 2 should be upserted");
    /// - transaction type `ValidatorTransaction::ObservedJWKUpdate` is reused;
    /// - while a key-level update is mostly represented by a new type `KeyLevelUpdate` locally,
    ///   For simplicity, it is represented by type `ProviderJWKs` (used to represent issuer-level update)
    ///   in JWK Consensus messages, in validator transactions, and in Move.
    const JWK_CONSENSUS_PER_KEY_MODE: u64 = 92;

    public fun get_jwk_consensus_per_key_mode_feature(): u64 {
        JWK_CONSENSUS_PER_KEY_MODE
    }

    public fun is_jwk_consensus_per_key_mode_enabled(): bool {
        is_enabled(JWK_CONSENSUS_PER_KEY_MODE)
    }

    /// Whether orderless transactions are enabled.
    /// Lifetime: transient
    const ORDERLESS_TRANSACTIONS: u64 = 94;

    public fun get_orderless_transactions_feature(): u64 {
        ORDERLESS_TRANSACTIONS
    }

    public fun orderless_transactions_enabled(): bool {
        is_enabled(ORDERLESS_TRANSACTIONS)
    }

    /// Whether to calculate the transaction fee for distribution.
    const CALCULATE_TRANSACTION_FEE_FOR_DISTRIBUTION: u64 = 96;

    public fun get_calculate_transaction_fee_for_distribution_feature(): u64 {
        CALCULATE_TRANSACTION_FEE_FOR_DISTRIBUTION
    }

    public fun is_calculate_transaction_fee_for_distribution_enabled(): bool {
        is_enabled(CALCULATE_TRANSACTION_FEE_FOR_DISTRIBUTION)
    }

    /// Whether to distribute transaction fee to validators.
    const DISTRIBUTE_TRANSACTION_FEE: u64 = 97;

    public fun get_distribute_transaction_fee_feature(): u64 {
        DISTRIBUTE_TRANSACTION_FEE
    }

    public fun is_distribute_transaction_fee_enabled(): bool {
        is_enabled(DISTRIBUTE_TRANSACTION_FEE)
    }

    #[deprecated]
    public fun get_monotonically_increasing_counter_feature(): u64 {
        abort error::invalid_argument(EINVALID_FEATURE)
    }

    #[deprecated]
    public fun is_monotonically_increasing_counter_enabled(): bool {
        true
    }

    /// Whether function reflection is enabled.
    const FUNCTION_REFLECTION: u64 = 105;

    public fun get_function_reflection_feature(): u64 {
        FUNCTION_REFLECTION
    }

    public fun is_function_reflection_enabled(): bool {
        is_enabled(FUNCTION_REFLECTION)
    }

    /// Whether SLH-DSA-SHA2-128s signature scheme is enabled for transaction authentication.
    /// Lifetime: transient
    const SLH_DSA_SHA2_128S_SIGNATURE: u64 = 107;

    public fun get_slh_dsa_sha2_128s_signature_feature(): u64 {
        SLH_DSA_SHA2_128S_SIGNATURE
    }

    public fun slh_dsa_sha2_128s_signature_enabled(): bool {
        is_enabled(SLH_DSA_SHA2_128S_SIGNATURE)
    }

    /// Whether the encrypted mempool feature is enabled.
    const ENCRYPTED_TRANSACTIONS: u64 = 108;

    public fun get_encrypted_transactions_feature(): u64 {
        ENCRYPTED_TRANSACTIONS
    }

    public fun is_encrypted_transactions_enabled(): bool {
        is_enabled(ENCRYPTED_TRANSACTIONS)
    }

    /// Whether multisig script payloads are enabled. Allows multisig accounts to
    /// propose and execute Move script payloads, not just entry functions.
    const MULTISIG_SCRIPT: u64 = 110;

    public fun get_multisig_script_feature(): u64 {
        MULTISIG_SCRIPT
    }

    public fun is_multisig_script_enabled(): bool {
        is_enabled(MULTISIG_SCRIPT)
    }

    /// Whether the transaction limits feature is enabled. Allows transactions
    /// to request higher execution/IO gas limits backed by staking voting power.
    const TRANSACTION_LIMITS: u64 = 111;

    public fun get_transaction_limits_feature(): u64 {
        TRANSACTION_LIMITS
    }

    public fun is_transaction_limits_enabled(): bool {
        is_enabled(TRANSACTION_LIMITS)
    }

    /// Whether the storage slot natives are enabled.
    const STORAGE_SLOT_NATIVES: u64 = 113;

    public fun is_storage_slot_natives_enabled(): bool {
        is_enabled(STORAGE_SLOT_NATIVES)
    }

    /// Whether the multisig timelock feature is enabled.
    const MULTISIG_TIMELOCK: u64 = 115;

    public fun get_multisig_timelock_feature(): u64 {
        MULTISIG_TIMELOCK
    }

    public fun is_multisig_timelock_enabled(): bool {
        is_enabled(MULTISIG_TIMELOCK)
    }

    /// When enabled, per-block hot-state promotions are persisted through the block
    /// epilogue: the promotion set is embedded into the block epilogue transaction
    /// payload (`BlockEpiloguePayload::V2`), and every transaction output in the block
    /// uses the V1 write-set format, which encodes hot-state changes in its serialized
    /// writes.
    /// Lifetime: permanent
    const HOTNESS_IN_EPILOGUE: u64 = 116;

    /// When enabled, execution assembles `TransactionInfoV1` instead of `TransactionInfoV0`.
```
