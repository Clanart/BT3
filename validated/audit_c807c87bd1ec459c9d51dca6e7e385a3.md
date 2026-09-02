[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/builder/sighash.rs (L50-80)
```rust
    pub fn get_num_required_nofn_sigs(&self, deposit_data: &DepositData) -> usize {
        deposit_data.get_num_operators()
            * self.protocol_paramset().num_round_txs
            * self.protocol_paramset().num_signed_kickoffs
            * self.get_num_required_nofn_sigs_per_kickoff(deposit_data)
    }

    /// Returns the number of required operator signatures for a deposit.
    ///
    /// # Arguments
    /// * `deposit_data` - The deposit data for which to calculate required signatures.
    ///
    /// # Returns
    /// The number of required operator signatures for the deposit.
    pub fn get_num_required_operator_sigs(&self, deposit_data: &DepositData) -> usize {
        self.protocol_paramset().num_round_txs
            * self.protocol_paramset().num_signed_kickoffs
            * self.get_num_required_operator_sigs_per_kickoff(deposit_data)
    }

    /// Returns the number of required N-of-N signatures per kickoff for a deposit.
    ///
    /// # Arguments
    /// * `deposit_data` - The deposit data for which to calculate required signatures per kickoff.
    ///
    /// # Returns
    /// The number of required N-of-N signatures per kickoff.
    pub fn get_num_required_nofn_sigs_per_kickoff(&self, deposit_data: &DepositData) -> usize {
        7 + 4 * deposit_data.get_num_verifiers()
            + bitvm_client::ClementineBitVMPublicKeys::number_of_assert_txs() * 2
    }
```

**File:** core/src/builder/sighash.rs (L255-281)
```rust
                    let mut sum = 0;
                    let mut kickoff_txid = None;
                    for (tx_type, txhandler) in txhandlers.iter() {
                        let sighashes = txhandler.calculate_shared_txins_sighash(EntityType::VerifierDeposit, partial)?;
                        sum += sighashes.len();
                        for sighash in sighashes {
                            yield sighash;
                        }
                        if tx_type == &TransactionType::Kickoff {
                            kickoff_txid = Some(txhandler.get_txid());
                        }
                    }

                    match (yield_kickoff_txid, kickoff_txid) {
                        (true, Some(kickoff_txid)) => {
                            yield (TapSighash::all_zeros(), partial.complete_with_kickoff_txid(*kickoff_txid));
                        }
                        (true, None) => {
                            Err(eyre::eyre!("Kickoff txid not found in sighash stream"))?;
                        }
                        _ => {}
                    }


                    if sum != config.get_num_required_nofn_sigs_per_kickoff(&deposit_data) {
                        Err(eyre::eyre!("NofN sighash count does not match: expected {0}, got {1}", config.get_num_required_nofn_sigs_per_kickoff(&deposit_data), sum))?;
                    }
```
