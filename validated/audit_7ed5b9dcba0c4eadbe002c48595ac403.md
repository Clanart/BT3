### Title
Scanner and transaction builder accept arbitrarily small (dust/zero-value) inputs, letting an unprivileged sender force the wallet to burn more in fees than the deposited value — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs), [File: networks/bitcoin/src/wallet/send.rs](networks/bitcoin/src/wallet/send.rs))

### Summary
Analogous to the Perennial issue where a user can deposit collateral below `minLiquidationFee` and force the protocol to pay out more than was deposited, `bitcoin-serai` enforces a minimum output value (`DUST = 546`) on *payments* and *change*, but enforces no minimum value on *incoming* outputs. `Scanner::scan_transaction` registers any output paying to a known script regardless of value, and `SignableTransaction::new` sums input values without checking that each input is worth more than the ~57 vbytes it adds to the transaction. An attacker can dust the wallet's public script_pubkey with many tiny (even zero-value) outputs; every subsequent transaction the wallet builds that includes them pays more in fees than the inputs contribute — a guaranteed payout-to-network cost exceeding the deposit, repeatable at scale across many outputs in a single transaction, exactly mirroring the reported bug's economics.

### Finding Description
- `DUST` is defined as 546 and enforced only on *payments*: `for (_, amount) in payments { if *amount < DUST { Err(DustPayment) } }` (`send.rs:165-169`) and on *change* (`send.rs:229` `if value >= DUST`).
- Input acceptance has no lower bound anywhere:
  - `Scanner::scan_transaction` pushes a `ReceivedOutput` for every output whose `script_pubkey` matches, unconditionally on `output.value` (`mod.rs:199-214`). A 0-value or 1-sat `TxOut` to a registered offset script is accepted.
  - `ReceivedOutput::read` decodes any `TxOut` from consensus bytes with no value check (`mod.rs:122-134`).
  - `SignableTransaction::new` sums `input_sat` and only checks `input_sat < payment_sat + needed_fee` *in aggregate* (`send.rs:215-221`). There is no per-input floor, so inputs whose individual value is less than the marginal fee they incur are included.
- Each added input costs ≈57 vbytes (per the code's own accounting comment in `processor/src/networks/bitcoin.rs:606-620`: 230 weight units ≈ 57 vbytes). At any nonzero `fee_per_vbyte`, an input worth less than `57 * fee_per_vbyte` sats is net-negative, yet the aggregate `NotEnoughFunds` check only fails when the *total* runs negative — the deficit is silently absorbed by legitimate inputs, and without change it is absorbed into the fee.

### Impact Explanation
An unprivileged party who learns any wallet script_pubkey (all deposit/change addresses are public on-chain) can broadcast dust outputs to it. `scan_transaction`/`scan_block`/`ReceivedOutput::read` ingest them as spendable inputs. Whenever the builder selects them, the transaction pays `fee_per_vbyte * 57` sats per input while gaining as little as 0 sats — the protocol/effective fee paid exceeds the attacker's deposit by up to `57 * fee_per_vbyte` sats per input, directly analogous to `minLiquidationFee - collateralBalance` being extracted per position. With many dust outputs this amortizes in a single transaction, matching the report's "open positions with many contracts at once … liquidate all of them in a single transaction." Additionally, 0-value `TxOut`s (legal consensus-wise if they ever appear in scanned data, and fully accepted by `read`) contribute pure fee burn. The spec's own threat model (`spec/processor/UTXO Management.md`) acknowledges dust-input-forced merges as a cost vector, but the in-scope wallet layer provides no defense.

### Likelihood Explanation
Sending dust to a known Bitcoin address requires no privilege and minimal cost; deposit addresses are inherently public once used on-chain. The inputs flow automatically through `Scanner::scan_block`/`scan_transaction` into `SignableTransaction::new` — no check anywhere rejects them, so inclusion depends only on the caller's input-selection policy, which this crate does not constrain. Feasibility is high whenever inputs are selected by value-agnostic means (e.g., consolidation or "use all received outputs").

### Recommendation
Enforce a minimum input value, mirroring the existing payment/change checks:
- In `SignableTransaction::new` (`send.rs`, after `inputs.is_empty()` check), reject or skip any `ReceivedOutput` whose `value() < DUST` (or, more precisely, whose value is less than `fee_per_vbyte * 57`, the marginal cost of spending it).
- Optionally also filter in `Scanner::scan_transaction` so sub-dust outputs are never surfaced as spendable, and/or in `ReceivedOutput::read` for defense-in-depth.
- This is the direct analog of the report's recommendation: enforce a lower bound (`collateralAfterFees >= minLiquidationFee` ↔ `input.value >= cost to spend it`) at the boundary where untrusted value enters the system.

### Proof of Concept
1. Compute the wallet's script: `p2tr_script_buf(group_key)` (public from any prior deposit).
2. Attacker broadcasts a transaction with, e.g., 100 outputs of 100 sats each paying that script (100 sats < DUST, and < 57 * fee for typical fee rates).
3. `Scanner::new(key).scan_block(&block)` returns 100 `ReceivedOutput`s; `ReceivedOutput::read` accepts serialized ones identically — no rejection.
4. A caller builds `SignableTransaction::new(dust_inputs, &payments, change, None, fee_per_vbyte)`. The only value checks are `amount < DUST` on payments and `input_sat < payment_sat + needed_fee` in aggregate; the 100 dust inputs pass and each adds ~57 vbytes. The resulting `fee()` (`prevouts_sum - outputs_sum`) shows the wallet paying `100 * 57 * fee_per_vbyte` sats for `100 * 100` sats of input — a net burn proportional to dust count, funded by the wallet's legitimate inputs/change, with zero defense available in this crate's API.