### Title
`Scanner::scan_block` credits immature coinbase outputs as spendable `ReceivedOutput`s, enabling an invalid spend of credited vault funds - (File: networks/bitcoin/src/wallet/mod.rs)

### Summary
The GPT exploit abused a broken fee/balance accounting mechanism: an unprivileged party could cause the victim contract to treat manipulated on-chain balances as real, spendable value. The reachable analog in Serai's in-scope code is the Bitcoin `Scanner`: `scan_block` iterates over *all* transactions in a block, including `block.txdata[0]` (the coinbase), and returns any output paying to a registered `script_pubkey` as a `ReceivedOutput` — a type documented as "a spendable output" — with no indication that the output is immature. A coinbase output cannot be spent until 100 confirmations (BIP-30 / consensus rule), yet Serai's confirmation depth is only 6. A permissionless miner can send a coinbase payout to the vault's P2TR `script_pubkey`, and the scanner reports it identically to a normal, spendable deposit.

### Finding Description
`Scanner::scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227` scans `block.txdata` in full, including `txdata[0]`, the coinbase transaction. It delegates to `scan_transaction` (lines 199-214), which matches outputs purely by `script_pubkey` and constructs a `ReceivedOutput` carrying only `offset`, `output`, and `outpoint` (lines 89-97) — there is no field distinguishing coinbase/immature outputs from spendable ones.

The doc comment acknowledges the issue ("This will also scan the coinbase transaction which is bound by maturity. If received outputs must be immediately spendable, a post-processing pass is needed") — however, the API itself still produces a value that is contractually a "spendable output" but is not spendable, and nothing at the type level enforces the required filtering. `SignableTransaction::new` (`networks/bitcoin/src/wallet/send.rs:150-256`) accepts any `Vec<ReceivedOutput>` as `inputs` and builds a transaction spending them without any maturity check; the resulting signed transaction is consensus-invalid (`bad-txns-premature-spend-of-coinbase`) if it consumes a coinbase younger than 100 blocks.

Reachability by an unprivileged party: mining a block is permissionless. Any miner can place an output paying to the vault's tweaked P2TR `script_pubkey` (which is a public value — it's the deposit address) inside their coinbase transaction. This is a "Bitcoin transaction they send" / public on-chain input, analogous to the attacker dusting the pair contract in the GPT exploit to corrupt its balance accounting.

### Impact Explanation
Funds are reported received that are not spendable: the scanner returns a `ReceivedOutput` indistinguishable from mature deposits, so credited balance includes value that cannot be moved for 100 blocks. If such an output is fed to `SignableTransaction::new` and the transaction is signed (threshold signing of a real transaction — not a test path), the broadcast transaction is rejected by consensus. Consequences:

- The credited "balance" overstates spendable funds; withdrawals/accounting that assume it is liquid will fail or produce invalid transactions, stalling payouts.
- Because `scan_transaction`/`scan_block` produce identical `ReceivedOutput`s for coinbase and normal outputs, downstream code cannot recover correct accounting without out-of-band knowledge of which tx was the coinbase.
- Analogous impact to the report: corrupted balance accounting driven by an untrusted on-chain transfer, reachable without any privileged position.

Severity: Medium — availability/accounting impact (consensus-invalid spends, phantom credited balance), no direct theft of funds since the immature output remains spendable after maturity.

### Likelihood Explanation
Low-to-moderate likelihood, real impact when hit:

- Requires a miner to pay to the vault's `script_pubkey` in a coinbase. This is permissionless but requires the miner to win a block while deliberately crafting the payout; miners have no economic reason to do this accidentally (mining pools pay out to their own addresses), so it is a deliberate griefing action costing the attacker nothing beyond normal mining.
- Exploitation also requires the consumer to use `scan_block` directly rather than filtering coinbase (the doc comment explicitly warns a pass is needed, which is a partial mitigation — but the invariant is not enforced in code, and `ReceivedOutput` carries no maturity flag, making misuse silent).
- Once the output is returned, `SignableTransaction::new` and signing proceed unconditionally — there is no second check anywhere in the in-scope code.

### Recommendation
- Track coinbase/maturity status on `ReceivedOutput` (e.g., a `maturity_height`/`is_coinbase` field populated by `scan_block`), and have `SignableTransaction::new` reject immature inputs rather than relying on callers to filter.
- Alternatively, have `scan_block` skip `block.txdata[0]` entirely and expose a separate `scan_coinbase` API, making the dangerous behavior opt-in instead of default.
- At minimum, since `CONFIRMATIONS` is 6 while coinbase maturity is 100, any crediting path must additionally require ≥100 confirmations for coinbase-derived outputs.

### Proof of Concept
Conceptual (regtest-style) sequence:

```rust
// networks/bitcoin/src/wallet/mod.rs — scan_block accepts the coinbase
// tx without distinguishing it:
//   for tx in &block.txdata { res.extend(self.scan_transaction(tx)); }

// 1. A miner mines a block whose coinbase output pays to the vault:
//    script_pubkey = p2tr_script_buf(vault_key).unwrap()
let block: Block = mined_block_with_coinbase_paying(vault_script);

// 2. Any downstream code scanning the block sees it as spendable:
let scanner = Scanner::new(vault_key).unwrap();
let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput { offset: ZERO, outpoint: (coinbase_txid, 0) }
// indistinguishable from a mature deposit.

// 3. It is accepted as a spendable input:
let tx = SignableTransaction::new(outputs, &payments, None, None, fee).unwrap();
// 4. After threshold signing, broadcasting fails consensus checks:
//    "bad-txns-premature-spend-of-coinbase" — the credited funds were
//    reported received but are not spendable for 100 blocks.
```

Supporting code: `scan_block` iterates the coinbase at `networks/bitcoin/src/wallet/mod.rs:221-227`; `scan_transaction` emits `ReceivedOutput` with no maturity data at lines 199-214; `ReceivedOutput` is declared "A spendable output" at line 88-97; `SignableTransaction::new` consumes inputs unconditionally at `networks/bitcoin/src/wallet/send.rs:150-256`; confirmation depth is `CONFIRMATIONS: usize = 6` (far below the 100-block maturity), so outputs are credited well before coinbase maturity.

Caveat: the doc comment on `scan_block` does warn that a post-processing pass is required, so the severity of this finding hinges on whether callers perform that filtering — that caller code is outside the in-scope crates and could not be verified. Even so, `ReceivedOutput` carries no way to express immaturity, so the "spendable output" contract is violated at the API boundary itself.