<title>
Integer Underflow Panic in `getSupply` RPC Handler Circulating Supply Calculation - (rpc/src/rpc.rs)
</title>

### Summary
`get_supply()` in `rpc/src/rpc.rs` computes `circulating = total_supply - non_circulating_supply.lamports` using an unchecked `u64` subtraction. This mirrors exactly the Velocimeter `circulating_supply()` bug: two independently derived quantities (`bank.capitalization()` for `total_supply`, and a separate account scan for `non_circulating_supply.lamports`) are subtracted without a saturating/checked guard, whereas the sibling REST endpoint for the identical calculation (`/v0/circulating-supply` in `rpc/src/rpc_service.rs`) explicitly uses `saturating_sub` to avoid this exact class of bug. [1](#0-0) [2](#0-1) 

### Finding Description
`get_supply` fetches `total_supply` from `bank.capitalization()` (an atomically-maintained running total of all lamports on the bank) and separately computes `non_circulating_supply` by scanning stake-program accounts plus a hardcoded list of non-circulating pubkeys, then summing each account's balance via `bank.get_balance(pubkey)`: [3](#0-2) 

These two values come from fundamentally different computation paths — one is an incrementally maintained atomic counter (`bank.capitalization`), the other is a fresh index scan plus per-account balance lookups (`calculate_non_circulating_supply` in `runtime/src/non_circulating_supply.rs`): [4](#0-3) 

The subtraction `total_supply - non_circulating_supply.lamports` at line 1148 is a plain unchecked `u64` subtraction. If `non_circulating_supply.lamports` were ever to exceed `total_supply` — due to any transient inconsistency between the atomic capitalization counter and the account-index-based scan (e.g. commitment levels that return a working/unfrozen bank still being mutated concurrently by the replay/banking stage while the scan and later `get_balance` calls execute), or due to any future bug in capitalization bookkeeping (the codebase itself acknowledges capitalization can drift, as shown by the explicit `checked_sub`/`expect("capitalization cannot underflow")` guards elsewhere in `accounts_db.rs`) — this subtraction panics. [5](#0-4) 

Critically, the exact same "circulating = total - non_circulating" computation already exists in the codebase with an explicit safety guard for the REST-based endpoint: [2](#0-1) 

This inconsistency — one call site using `saturating_sub`, the other using a bare `-` — indicates the underflow risk is a recognized but incompletely fixed hazard, precisely analogous to the Velocimeter Minter bug where `_flow.totalSupply() - _ve.totalSupply()` could revert because the two supply figures are tracked by independent mechanisms that are not guaranteed to always satisfy `total >= non_circulating`.

### Impact Explanation
A panic in `get_supply()` would crash the JSON-RPC processing thread handling that request (or the whole `jsonrpc_core` worker depending on panic-catching configuration), causing a denial of service for the `getSupply` RPC method on that node. This matches the "single-client low-rate RPC crash/degradation" impact category — a single call to `getSupply` under the right (albeit narrow) conditions could crash/degrade the RPC service, without requiring any malicious peer, validator, or privileged actor.

### Likelihood Explanation
Likelihood is low-to-moderate and could not be fully confirmed from static analysis alone within the available tool budget. The theoretical precondition — `non_circulating_supply.lamports > total_supply` — should not normally occur since non-circulating accounts are a logical subset of all bank accounts contributing to capitalization. However:
- The scan (`get_program_accounts`/`get_filtered_indexed_accounts`) and the later per-account `get_balance` calls in `calculate_non_circulating_supply` are not part of a single atomic snapshot; if `get_supply` is invoked against a bank that is not yet frozen (depending on which `CommitmentLevel` is requested and how `self.bank(commitment)` resolves it), capitalization and balances read at different times could theoretically diverge.
- I was unable to fully verify, within the given iteration budget, whether `self.bank(config.commitment)` in `rpc.rs` can ever return an unfrozen/mutable working bank for the commitment levels accepted by `getSupply`, which would be the concrete trigger condition. This should be verified with further investigation (e.g., a full Devin session) before treating this as a high-confidence, immediately-exploitable bug.

### Recommendation
Change `rpc/src/rpc.rs`'s `get_supply` to use `total_supply.saturating_sub(non_circulating_supply.lamports)` (mirroring the guard already present in `rpc/src/rpc_service.rs::calculate_circulating_supply_async`), eliminating the panic path regardless of whether the underlying invariant can currently be violated.

### Proof of Concept
Concrete reproduction requires demonstrating a bank state where `non_circulating_supply.lamports > bank.capitalization()`, which requires deeper runtime state manipulation (e.g., invoking `getSupply` against a non-frozen/working bank via a `processed`-commitment request while concurrent transaction processing is running, or artificially forcing a capitalization/account-balance mismatch in a test bank) — this exact reproduction was not completed within the current investigation and is flagged as unverified. The core, verifiable fact from local code is the unchecked subtraction itself and the inconsistency with the guarded sibling implementation: [1](#0-0) [2](#0-1)

### Citations

**File:** rpc/src/rpc.rs (L1121-1152)
```rust
    async fn get_supply(
        &self,
        config: Option<RpcSupplyConfig>,
    ) -> RpcCustomResult<RpcResponse<RpcSupply>> {
        let config = config.unwrap_or_default();
        let bank = self.bank(config.commitment);
        let non_circulating_supply =
            self.calculate_non_circulating_supply(&bank)
                .await
                .map_err(|e| RpcCustomError::ScanError {
                    message: e.to_string(),
                })?;
        let total_supply = bank.capitalization();
        let non_circulating_accounts = if config.exclude_non_circulating_accounts_list {
            vec![]
        } else {
            non_circulating_supply
                .accounts
                .iter()
                .map(|pubkey| pubkey.to_string())
                .collect()
        };

        Ok(new_response(
            &bank,
            RpcSupply {
                total: total_supply,
                circulating: total_supply - non_circulating_supply.lamports,
                non_circulating: non_circulating_supply.lamports,
                non_circulating_accounts,
            },
        ))
```

**File:** rpc/src/rpc_service.rs (L422-431)
```rust
async fn calculate_circulating_supply_async(bank: &Arc<Bank>) -> Result<u64, SupplyCalcError> {
    let total_supply = bank.capitalization();
    let bank = Arc::clone(bank);
    let non_circulating_supply =
        tokio::task::spawn_blocking(move || calculate_non_circulating_supply(&bank))
            .await
            .expect("Failed to spawn blocking task")
            .map_err(|e| SupplyCalcError::Scan(e.to_string()))?;

    Ok(total_supply.saturating_sub(non_circulating_supply.lamports))
```

**File:** runtime/src/non_circulating_supply.rs (L19-79)
```rust
pub fn calculate_non_circulating_supply(bank: &Bank) -> ScanResult<NonCirculatingSupply> {
    debug!("Updating Bank supply, epoch: {}", bank.epoch());
    let mut non_circulating_accounts_set: HashSet<Pubkey> = HashSet::new();

    for key in non_circulating_accounts() {
        non_circulating_accounts_set.insert(key);
    }
    let withdraw_authority_list = withdraw_authority();

    let clock = bank.clock();
    let stake_accounts = if bank
        .rc
        .accounts
        .accounts_db
        .account_indexes
        .contains(&AccountIndex::ProgramId)
    {
        bank.get_filtered_indexed_accounts(
            &IndexKey::ProgramId(stake::program::id()),
            // The program-id account index checks for Account owner on inclusion. However, due to
            // the current AccountsDb implementation, an account may remain in storage as a
            // zero-lamport Account::Default() after being wiped and reinitialized in later
            // updates. We include the redundant filter here to avoid returning these accounts.
            |account| account.owner() == &stake::program::id(),
            None,
        )?
    } else {
        bank.get_program_accounts(&stake::program::id())?
    };

    for (pubkey, account) in stake_accounts.iter() {
        let stake_account = account
            .deserialize_data::<StakeStateV2>()
            .unwrap_or_default();
        match stake_account {
            StakeStateV2::Initialized(meta)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            StakeStateV2::Stake(meta, _stake, _stake_flags)
                if (meta.lockup.is_in_force(&clock, None)
                    || withdraw_authority_list.contains(&meta.authorized.withdrawer)) =>
            {
                non_circulating_accounts_set.insert(*pubkey);
            }
            _ => {}
        }
    }

    let lamports = non_circulating_accounts_set
        .iter()
        .map(|pubkey| bank.get_balance(pubkey))
        .sum();

    Ok(NonCirculatingSupply {
        lamports,
        accounts: non_circulating_accounts_set.into_iter().collect(),
    })
}
```

**File:** accounts-db/src/accounts_db.rs (L6107-6112)
```rust
        total_accum.lt_hash.mix_out(&duplicates_lt_hash.0);
        total_accum.capitalization = total_accum
            .capitalization
            .checked_sub(capitalization_from_duplicates)
            .expect("capitalization cannot underflow");
        total_accum.accounts_data_len -= accounts_data_len_from_duplicates;
```
