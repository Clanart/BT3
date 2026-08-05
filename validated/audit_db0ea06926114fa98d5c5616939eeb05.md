[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** runtime/src/bank/accounts_lt_hash.rs (L51-57)
```rust
        // process accounts in reverse because we must only count the latest version of each account
        for index in (0..accounts.len()).rev() {
            let address = accounts.pubkey(index);
            if !seen_accounts.insert(*address) {
                // we've already enqueued a newer update for the same account; skip this one
                continue;
            }
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L58-68)
```rust
            let prev_account = self
                .rc
                .accounts
                .load_with_fixed_root_do_not_populate_read_cache(&self.ancestors, address)
                .map(|(account, _slot)| account);
            let curr_account = accounts.account(index, |account| {
                (account.lamports() != 0).then(|| account.take_account())
            });
            if prev_account.is_none() && curr_account.is_none() {
                // the account was ephemeral; skip it
            } else {
```

**File:** runtime/src/bank/accounts_lt_hash.rs (L308-322)
```rust
    fn process(accum_lt_hash: &mut LtHash, update: AccountsLtHashUpdate) {
        let AccountsLtHashUpdate {
            address,
            prev_account,
            curr_account,
        } = update;
        if let Some(prev_account) = prev_account {
            let prev_lt_hash = AccountsDb::lt_hash_account(&prev_account, &address);
            accum_lt_hash.mix_out(&prev_lt_hash.0);
        }
        if let Some(curr_account) = curr_account {
            let curr_lt_hash = AccountsDb::lt_hash_account(&curr_account, &address);
            accum_lt_hash.mix_in(&curr_lt_hash.0);
        }
    }
```
