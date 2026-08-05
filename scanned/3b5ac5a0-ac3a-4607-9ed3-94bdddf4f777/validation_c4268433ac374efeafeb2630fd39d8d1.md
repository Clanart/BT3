Now I have enough detail to write up the analog. This is the strongest structural match to the "wrong init code hash" bug class: a hardcoded/optional expected-value constant (`verified_build_hash: Option<Hash>`) used to authenticate untrusted bytecode before it's substituted into the trust root of the runtime (a builtin program), and in practice this constant is set to `None` for every currently-defined production migration in `BUILTINS`.## Analysis

The external report's broken invariant: a hardcoded cryptographic value used to authenticate/derive an on-chain artifact becomes stale relative to the real content it's supposed to validate, and downstream code trusts the derived/verified result without any fallback check, causing wholesale malfunction/wrong-artifact acceptance.

The closest structural analog in Agave is the **Core BPF migration build-hash verification**, which optionally checks an expected SHA-256 hash of the replacement bytecode before that bytecode permanently replaces a native builtin program (e.g. `system_program`, `vote_program`, `bpf_loader*`, `compute_budget`, `zk_token_proof_program`) cluster-wide.

### Title
Core BPF migration accepts unverified buffer bytecode when `verified_build_hash` is `None` — (`builtins/src/core_bpf_migration.rs`)

### Summary
`CoreBpfMigrationConfig::verified_build_hash` is an `Option<Hash>` meant to pin the expected build hash of the BPF bytecode that will replace a native builtin. Every current production entry in `BUILTINS`/`test_only` configs sets this field to `None` [1](#0-0) , so `migrate_builtin_to_core_bpf` falls back to `SourceBuffer::new_checked` (no hash check at all) rather than `new_checked_with_verified_build_hash` [2](#0-1) . This mirrors the Uniswap bug: a value meant to bind the migration to a specific, verifiable bytecode is either absent or can silently diverge from the bytecode actually deployed, and the consuming code has no other independent check of program identity/content before it becomes the new trust root for the entire cluster.

### Finding Description
`SourceBuffer::new_checked` only validates that the buffer account exists, is owned by `bpf_loader_upgradeable`, and deserializes as `UpgradeableLoaderState::Buffer` — it performs **no content verification** of the ELF bytes [3](#0-2) . Only `new_checked_with_verified_build_hash` computes `sha256(buffer_program_data)` and compares it against `expected_hash`, erroring on mismatch [4](#0-3) .

`migrate_builtin_to_core_bpf` decides which path to take purely based on whether `config.verified_build_hash` is `Some` [2](#0-1) . The `upgrade_authority_address` sanity check is a separate, weaker control: it only verifies the buffer's stated authority pubkey matches config, and if `upgrade_authority_address` is `None` in config, that check is skipped entirely as well [5](#0-4) , [6](#0-5) .

Once the migration proceeds, the buffer's raw bytes are unconditionally deployed as the new implementation of the builtin via `directly_invoke_loader_v3_deploy`, the target program account is overwritten, and the builtin is removed from `builtin_program_ids` [7](#0-6) . There is no re-derivation of the bytecode from a canonical source at migration time — the only integrity guard available in the codebase (`verified_build_hash`) is opt-in and, per current shipped configs, universally opted out (`None`) for real builtins such as system/vote/bpf_loader/compute_budget/zk-proof programs [8](#0-7) .

This is structurally identical to the reported bug class: a hash meant to bind computed/derived state to the correct, expected artifact is either wrong or missing, and the consumer proceeds anyway, trusting whatever is at the configured address. In the Uniswap case the "wrong" derived address broke swapping; here, if the buffer's content diverges from what the developers intended (stale buffer, compromise of the pre-funded buffer account by whoever last had upgrade authority over it, or an operational mistake filling the buffer with the wrong/older/malicious build) and `verified_build_hash` is `None`, nothing in the runtime stops the divergent bytecode from becoming the new builtin for the entire cluster at the next epoch boundary.

### Impact Explanation
If accepted, this substitutes the actual executable code behind a core builtin (system, vote, bpf_loader family, compute_budget, zk-proof programs) cluster-wide with whatever bytes are in the source buffer account at the exact feature-activation slot, without runtime-level content verification. Because these are foundational programs invoked on virtually every transaction, wrong/incompatible bytecode would cause consensus-relevant divergence, transaction execution failures, or false acceptance/rejection of state across the whole cluster — categorically the same "total bricking of core functionality due to unauthenticated wrong content" impact class as the referenced report, escalated here to a runtime/consensus concern rather than a DeFi liquidity-pool bug.

### Likelihood Explanation
This is not attacker-triggerable by an ordinary unprivileged transaction — the migration itself is gated by feature-gate activation and controlled by a hardcoded `source_buffer_address`/`upgrade_authority_address` in the Agave source tree, which is an operational/release-engineering control rather than an on-chain permission check. The exposure is that the *code path itself* provides no independent enforcement that a hash check occurs; correctness depends entirely on developers remembering to set `verified_build_hash: Some(...)` per migration, and currently no shipped config does. This is best framed as a missing defense-in-depth invariant (an optional check that is never exercised) rather than a directly exploitable remote bug, which is why it is presented as a code-quality/consensus-safety gap analogous in *mechanism* (unverified hash-bound artifact substitution) to the cited report, not as a proven live exploit.

### Recommendation
Make `verified_build_hash` mandatory (non-`Option`) for all `CoreBpfMigrationConfig` entries used against real, non-test builtins, or otherwise enforce at compile/CI time that no production `BuiltinPrototype`/`StatelessBuiltinPrototype` can ship with `verified_build_hash: None`, so that `SourceBuffer::new_checked_with_verified_build_hash` is unconditionally exercised in `migrate_builtin_to_core_bpf` before any Core BPF migration is allowed to overwrite a builtin.

### Proof of Concept
Static-analysis PoC (no live cluster needed): every production entry in `BUILTINS`'s `#[cfg(feature = "dev-context-only-utils")] core_bpf_migration_config` uses `verified_build_hash: None` [9](#0-8) . Tracing `migrate_builtin_to_core_bpf` confirms that with `verified_build_hash: None`, `SourceBuffer::new_checked` is called instead of the hash-checked variant [2](#0-1) , and the unit test `test_migrate_builtin` demonstrates a full successful migration using `verified_build_hash: None` with only an authority check applied [10](#0-9) , versus `test_migrate_fail_verified_build_mismatch`, which shows the hash check is only enforced when explicitly configured with `Some(...)` [11](#0-10) .

### Citations

**File:** builtins/src/lib.rs (L133-281)
```rust
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_system_program",
        };
    }

    pub mod vote_program {
        pub mod feature {
            solana_pubkey::declare_id!("5wDLHMasPmtrcpfRZX67RVkBXBbSTQ9S4C8EJomD3yAk");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("6T9s4PTcHnpq2AVAqoCbJd4FuHsdD99MjSUEbS7qb1tT");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("2N4JfyYub6cWUP9R4JrsFHv6FYKT7JnoRX8GQUH9MdT3");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_vote_program",
        };
    }

    pub mod solana_bpf_loader_deprecated_program {
        pub mod feature {
            solana_pubkey::declare_id!("8gpakCv5Pk5PZGv9RUjzdkk2GVQPGx12cNRUDMQ3bP86");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("DveUYB5m9G3ce4zpV3fxg9pCNkvH1wDsyd8XberZ47JL");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("8Y5VTHdadnz4rZZWdUA4Qq2m2zWoCwwtb38spPZCXuGU");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_bpf_loader_deprecated_program",
        };
    }

    pub mod solana_bpf_loader_program {
        pub mod feature {
            solana_pubkey::declare_id!("8yEdUm4SaP1yNq2MczEVdrM48SucvZCTDSqjcAKfYfL6");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("2EWMYGJPuGLW4TexLLEMeXP2BkB1PXEKBFb698yw6LhT");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("3sQ9VZ1Lvuvs6NpFXFV3ByFAf52ajPPdXwuhYERJR3iJ");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_bpf_loader_program",
        };
    }

    pub mod solana_bpf_loader_upgradeable_program {
        pub mod feature {
            solana_pubkey::declare_id!("oPQbVjgoQ7SaQmzZiiHW4xqHbh4BJqqrFhxEJZiMiwY");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("6bTmA9iefD57GDoQ9wUjG8SeYkSpRw3EkKzxZCbhkavq");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("CuJvJY1K2wx82oLrQGSSWtw4AF7nVifEHupzSC2KEcq5");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_bpf_loader_upgradeable_program",
        };
    }

    pub mod compute_budget_program {
        pub mod feature {
            solana_pubkey::declare_id!("D39vUspVfhjPVD7EtMJZrA5j1TSMp4LXfb43nxumGdHT");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("KfX1oLpFC5CwmFeSgXrNcXaouKjFkPuSJ4UsKb3zKMX");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("HGTbQhaCXNTbpgpLb2KNjqWSwpJyb2dqDB66Lc3Ph4aN");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_compute_budget_program",
        };
    }

    pub mod zk_token_proof_program {
        pub mod feature {
            solana_pubkey::declare_id!("GfeFwUzKP9NmaP5u4VfnFgEvQoeQc2wPgnBFrUZhpib5");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("Ffe9gL8vXraBkiv3HqbLvBqY7i9V4qtZxjH83jYYDe1V");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("6zkXWHR8YeCvfMqHwyiz2n7g6hMUKCFhrVccZZTDk4ei");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_zk_token_proof_program",
        };
    }

    pub mod zk_elgamal_proof_program {
        pub mod feature {
            solana_pubkey::declare_id!("EYtuxScWqGWmcPEDmeUsEt3iPkvWE26EWLfSxUvWP2WN");
        }
        pub mod source_buffer {
            solana_pubkey::declare_id!("AaVrLPurAUmjw6XRNGr6ezQfHaJWpBGHhcRSJmNjoVpQ");
        }
        pub mod upgrade_authority {
            solana_pubkey::declare_id!("EyGkQYHgynUdvdNPNiWbJQk9roFCexgdJQMNcWbuvp78");
        }
        pub const CONFIG: super::CoreBpfMigrationConfig = super::CoreBpfMigrationConfig {
            source_buffer_address: source_buffer::id(),
            upgrade_authority_address: Some(upgrade_authority::id()),
            feature_id: feature::id(),
            migration_target: super::CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "migrate_builtin_to_core_bpf_zk_elgamal_proof_program",
        };
    }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L82-93)
```rust
        if let UpgradeableLoaderState::Buffer {
            authority_address: buffer_authority,
        } = bincode::deserialize(&source.buffer_account.data()[..buffer_metadata_size])?
        {
            if let Some(provided_authority) = upgrade_authority_address
                && upgrade_authority_address != buffer_authority
            {
                return Err(CoreBpfMigrationError::UpgradeAuthorityMismatch(
                    provided_authority,
                    buffer_authority,
                ));
            }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L238-246)
```rust
        let source = if let Some(expected_hash) = config.verified_build_hash {
            SourceBuffer::new_checked_with_verified_build_hash(
                self,
                &config.source_buffer_address,
                expected_hash,
            )?
        } else {
            SourceBuffer::new_checked(self, &config.source_buffer_address)?
        };
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L268-306)
```rust
        // Deploy the new target Core BPF program.
        // This step will validate the program ELF against the current runtime
        // environment, as well as update the program cache.
        self.directly_invoke_loader_v3_deploy(
            &target.program_address,
            new_target_program_data_account.data(),
        )?;

        // Calculate the lamports to burn.
        // The target program account will be replaced, so burn its lamports.
        // The target program data account might have lamports if it existed,
        // so burn its lamports if any.
        // The source buffer account will be cleared, so burn its lamports.
        // The two new program accounts will need to be funded.
        let lamports_to_burn = checked_add(
            target.program_account.lamports(),
            source.buffer_account.lamports(),
        )
        .and_then(|v| checked_add(v, target.program_data_account_lamports))?;
        let lamports_to_fund = checked_add(
            new_target_program_account.lamports(),
            new_target_program_data_account.lamports(),
        )?;
        self.update_captalization(lamports_to_burn, lamports_to_fund)?;

        // Store the new program accounts and clear the source buffer account.
        self.store_account(&target.program_address, &new_target_program_account);
        self.store_account(
            &target.program_data_address,
            &new_target_program_data_account,
        );
        self.store_account(&source.buffer_address, &AccountSharedData::default());

        // Remove the built-in program from the bank's list of built-ins.
        self.transaction_processor
            .builtin_program_ids
            .write()
            .unwrap()
            .remove(&target.program_address);
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L826-838)
```rust
        let core_bpf_migration_config = CoreBpfMigrationConfig {
            source_buffer_address,
            upgrade_authority_address,
            feature_id: Pubkey::new_unique(),
            migration_target: CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: None,
            datapoint_name: "test_migrate_builtin",
        };

        // Perform the migration.
        let migration_slot = bank.slot();
        bank.migrate_builtin_to_core_bpf(&builtin_id, &core_bpf_migration_config, true)
            .unwrap();
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L1005-1018)
```rust
        let core_bpf_migration_config = CoreBpfMigrationConfig {
            source_buffer_address,
            upgrade_authority_address: None,
            feature_id: Pubkey::new_unique(),
            migration_target: CoreBpfMigrationTargetType::Builtin,
            verified_build_hash: Some(Hash::default()),
            datapoint_name: "test_migrate_builtin",
        };

        assert_matches!(
            bank.migrate_builtin_to_core_bpf(&builtin_id, &core_bpf_migration_config, true)
                .unwrap_err(),
            CoreBpfMigrationError::BuildHashMismatch(_, _)
        )
```

**File:** runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs (L19-48)
```rust
impl SourceBuffer {
    /// Collects the details of a buffer account and verifies it exists, is
    /// owned by the upgradeable loader, and has the correct state.
    pub(crate) fn new_checked(
        bank: &Bank,
        buffer_address: &Pubkey,
    ) -> Result<Self, CoreBpfMigrationError> {
        // The buffer account should exist.
        let buffer_account = bank
            .get_account_with_fixed_root(buffer_address)
            .ok_or(CoreBpfMigrationError::AccountNotFound(*buffer_address))?;

        // The buffer account should be owned by the upgradeable loader.
        if buffer_account.owner() != &bpf_loader_upgradeable::id() {
            return Err(CoreBpfMigrationError::IncorrectOwner(*buffer_address));
        }

        // The buffer account should have the correct state.
        let buffer_metadata_size = UpgradeableLoaderState::size_of_buffer_metadata();
        if buffer_account.data().len() >= buffer_metadata_size
            && let UpgradeableLoaderState::Buffer { .. } =
                bincode::deserialize(&buffer_account.data()[..buffer_metadata_size])?
        {
            return Ok(Self {
                buffer_address: *buffer_address,
                buffer_account,
            });
        }
        Err(CoreBpfMigrationError::InvalidBufferAccount(*buffer_address))
    }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/source_buffer.rs (L52-73)
```rust
    pub(crate) fn new_checked_with_verified_build_hash(
        bank: &Bank,
        buffer_address: &Pubkey,
        expected_hash: Hash,
    ) -> Result<Self, CoreBpfMigrationError> {
        let buffer = Self::new_checked(bank, buffer_address)?;
        let data = buffer.buffer_account.data();

        let offset = UpgradeableLoaderState::size_of_buffer_metadata();
        let end_offset = data.iter().rposition(|&x| x != 0).map_or(offset, |i| i + 1);
        let buffer_program_data = &data[offset..end_offset];
        let hash = solana_sha256_hasher::hash(buffer_program_data);

        if hash != expected_hash {
            return Err(CoreBpfMigrationError::BuildHashMismatch(
                hash,
                expected_hash,
            ));
        }

        Ok(buffer)
    }
```

**File:** programs/bpf_loader/src/lib.rs (L242-254)
```rust
            if let UpgradeableLoaderState::Buffer { authority_address } = buffer.get_state()? {
                if authority_address != authority_key {
                    ic_logger_msg!(log_collector, "Buffer and upgrade authority don't match");
                    return Err(InstructionError::IncorrectAuthority);
                }
                if !instruction_context.is_instruction_account_signer(7)? {
                    ic_logger_msg!(log_collector, "Upgrade authority did not sign");
                    return Err(InstructionError::MissingRequiredSignature);
                }
            } else {
                ic_logger_msg!(log_collector, "Invalid Buffer account");
                return Err(InstructionError::InvalidArgument);
            }
```
