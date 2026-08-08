### Title
Permissionless `ExtendProgram` griefs pending `ExtendProgram`/`Upgrade` transactions via same-slot dust front-run - (File: `programs/bpf_loader/src/lib.rs`)

### Summary
`common_extend_program()` enforces that a program's `ProgramData` account can only be touched by an `ExtendProgram`-class state update once per slot, by comparing the account's stored `slot` field to the current `clock.slot`. Because the public `ExtendProgram` instruction is invoked with `check_authority = false`, any unprivileged, unauthenticated user can call it on **any** upgradeable program's data account for the cost of a single dust-sized extension. This lets an attacker front-run and grief a pending, legitimate `ExtendProgram` (or any other operation that stamps the same `slot` value) transaction sitting in the mempool for the same target account, exactly matching the bug class in the referenced report (a strict "already acted upon this period" check that can be tripped by an unprivileged party with a trivial-cost transaction).

### Finding Description
`common_extend_program` is the shared handler for `UpgradeableLoaderInstruction::ExtendProgram`: [1](#0-0) 

Crucially, when dispatched from the standalone `ExtendProgram` instruction, `check_authority` is passed as `false`, meaning **no upgrade authority signature or match is required** to extend a program's data account — this operation is intentionally permissionless (payer just covers any rent-exempt delta): [2](#0-1) 

The function then reads the current slot and compares it against the `slot` value already recorded in the `ProgramData` account state: [3](#0-2) 

```rust
if clock_slot == slot {
    ic_logger_msg!(log_collector, "Program was extended in this block already");
    return Err(InstructionError::InvalidArgument);
}
```

This is the exact analog of the Aura `ExtraRewardsDistributor.rewardEpochs` ordering guard (`require(... rewardEpochs[...][len-1] < _epoch ...)`): a monotonic, "one write per period" invariant that any user can trip on behalf of another party, because the write path that advances the tracked value (`slot`) is permissionless and cheap. Here, `slot` is stamped on the `ProgramData` account by any successful `ExtendProgram` (or `Upgrade`/deploy, which also updates the same field) — see the state update after extension: [4](#0-3) 

A repository test explicitly documents this same-slot exclusivity behavior: [5](#0-4) 

Because any address can submit `ExtendProgram` with `additional_bytes = 1` (minimal dust cost — only the marginal rent-exempt lamports, if any, for one extra byte) targeting the same `programdata_address` a victim is about to extend, and because Solana transactions are visible in the mempool/gossip before landing, an attacker can observe a pending `ExtendProgram` transaction (e.g., part of a CLI `program extend`/`deploy --auto-extend` flow) and front-run it in the same slot with a 1-byte extension. The victim's transaction then hits the `clock_slot == slot` branch and reverts with `InstructionError::InvalidArgument`.

### Impact Explanation
This is a griefing/DoS vector against program deployment/upgrade tooling: it can repeatedly force the failure of legitimate `ExtendProgram` calls that are part of automated deploy/upgrade pipelines (e.g., CLI's `auto_extend` logic which conditionally emits an `extend_program` instruction before writing program bytes, seen in `extend_program_data_if_needed`). Repeated griefing costs the attacker only marginal rent-exempt lamports (dust) per attempt, while the victim pays a wasted transaction fee and must retry, delaying program upgrades/deployments. It does not corrupt state, escalate privilege, or cause memory-safety issues — the account ends up merely one slot "later" than intended, so severity is bounded to availability/cost griefing rather than a security-critical break, similar to how Aura acknowledged but downgraded severity of the original finding.

### Likelihood Explanation
Likelihood is moderate-to-low: the attacker needs to monitor the mempool/gossip for a victim's pending `ExtendProgram` (or related) transaction targeting a specific `programdata_address`, and successfully land a competing `ExtendProgram` transaction in the same slot before the victim's transaction is processed. This is feasible for anyone running a validator or with fast RPC access, but requires active targeting of a specific program's deployment window rather than being exploitable passively or at scale against arbitrary victims.

### Recommendation
Reconsider the "one state-changing operation per slot" invariant on `ProgramData`. Options: (1) track the last-modifying transaction/signer intent more granularly (e.g., allow multiple `ExtendProgram` calls in the same slot to coalesce rather than reverting the second one), or (2) require authority-gated extension (remove the fully permissionless nature of `ExtendProgram`) so that only the program's own upgrade authority (or its designated payer) can trigger the "already extended this slot" contention, closing off third-party griefing while preserving the same-slot dedup goal for legitimate flows.

### Proof of Concept
1. Attacker watches for a broadcast transaction containing `loader_v3_instruction::extend_program(program_id, payer, N)` targeting `programdata_address` P.
2. Attacker submits their own transaction containing `extend_program(program_id, attacker_payer, 1)` for the same P, with the same or higher priority fee, aiming to land in the same slot.
3. If the attacker's transaction confirms first, `programdata_account`'s `slot` field is set to `clock_slot` per [4](#0-3) .
4. The victim's transaction, still targeting the same slot, hits `clock_slot == slot` in `common_extend_program` and fails with `InstructionError::InvalidArgument`, per [6](#0-5) , exactly as reproduced by the repository's own `test_failed_extend_twice_in_same_slot` test at [7](#0-6) .

### Citations

**File:** programs/bpf_loader/src/lib.rs (L790-802)
```rust
        UpgradeableLoaderInstruction::ExtendProgram { additional_bytes } => {
            common_extend_program(invoke_context, additional_bytes, false)?;
        }
    }

    Ok(())
}

fn common_extend_program(
    invoke_context: &mut InvokeContext,
    additional_bytes: u32,
    check_authority: bool,
) -> Result<(), InstructionError> {
```

**File:** programs/bpf_loader/src/lib.rs (L898-912)
```rust
    let clock_slot = invoke_context
        .environment_config
        .sysvar_cache()
        .get_clock()
        .map(|clock| clock.slot)?;

    let upgrade_authority_address = if let UpgradeableLoaderState::ProgramData {
        slot,
        upgrade_authority_address,
    } = programdata_account.get_state()?
    {
        if clock_slot == slot {
            ic_logger_msg!(log_collector, "Program was extended in this block already");
            return Err(InstructionError::InvalidArgument);
        }
```

**File:** programs/bpf_loader/src/lib.rs (L987-993)
```rust
    let mut programdata_account =
        instruction_context.try_borrow_instruction_account(PROGRAM_DATA_ACCOUNT_INDEX)?;
    programdata_account.set_state(&UpgradeableLoaderState::ProgramData {
        slot: clock_slot,
        upgrade_authority_address,
    })?;

```

**File:** programs/bpf-loader-tests/tests/extend_program_ix.rs (L92-176)
```rust
async fn test_failed_extend_twice_in_same_slot() {
    let mut context = setup_test_context(LoaderV3Features {
        minimum_extend_program_size: false,
    })
    .await;
    let program_file = find_file("noop.so").expect("Failed to find the file");
    let data = read_file(program_file);
    let upgrade_authority = Keypair::new();

    let program_address = Pubkey::new_unique();
    let (programdata_address, _) = Pubkey::find_program_address(&[program_address.as_ref()], &id());
    add_upgradeable_loader_account(
        &mut context,
        &program_address,
        &UpgradeableLoaderState::Program {
            programdata_address,
        },
        UpgradeableLoaderState::size_of_program(),
        |_| {},
    )
    .await;
    let programdata_data_offset = UpgradeableLoaderState::size_of_programdata_metadata();
    let program_data_len = data.len() + programdata_data_offset;
    add_upgradeable_loader_account(
        &mut context,
        &programdata_address,
        &UpgradeableLoaderState::ProgramData {
            slot: 0,
            upgrade_authority_address: Some(upgrade_authority.pubkey()),
        },
        program_data_len,
        |account| account.data_as_mut_slice()[programdata_data_offset..].copy_from_slice(&data),
    )
    .await;

    let client = &mut context.banks_client;
    let payer = &context.payer;
    let recent_blockhash = context.last_blockhash;
    const ADDITIONAL_BYTES: u32 = 42;
    let transaction = Transaction::new_signed_with_payer(
        &[extend_program(
            &program_address,
            Some(&payer.pubkey()),
            ADDITIONAL_BYTES,
        )],
        Some(&payer.pubkey()),
        &[payer],
        recent_blockhash,
    );

    assert_matches!(client.process_transaction(transaction).await, Ok(()));
    let updated_program_data_account = client
        .get_account(programdata_address)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(
        updated_program_data_account.data().len(),
        program_data_len + ADDITIONAL_BYTES as usize
    );

    let recent_blockhash = client
        .get_new_latest_blockhash(&recent_blockhash)
        .await
        .unwrap();
    // Extending the program in the same slot should fail
    let transaction = Transaction::new_signed_with_payer(
        &[extend_program(
            &program_address,
            Some(&payer.pubkey()),
            ADDITIONAL_BYTES,
        )],
        Some(&payer.pubkey()),
        &[payer],
        recent_blockhash,
    );

    assert_matches!(
        client
            .process_transaction(transaction)
            .await
            .unwrap_err()
            .unwrap(),
        TransactionError::InstructionError(0, InstructionError::InvalidArgument)
    );
```
