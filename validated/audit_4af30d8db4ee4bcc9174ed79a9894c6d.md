### Title
Dust outputs are scanned and reported as spendable balance despite costing more in fees to spend than they are worth — (`File: networks/bitcoin/src/wallet/mod.rs`)

### Summary
The analog of "avoid a fee by fragmenting into tiny amounts" in Serai's bitcoin crate is that `Scanner` reports every transaction output paying to a tracked `script_pubkey` as a `ReceivedOutput` (received balance), with no check that the output's value covers the marginal fee required to spend it. An unprivileged sender can deposit outputs valued below their spend cost (or below `DUST`), which are then treated as received funds that are either unspendable or net-negative when consumed as transaction inputs.

### Finding Description
`Scanner::scan_transaction` matches `output.script_pubkey` against the registered script set and pushes a `ReceivedOutput` for any match, unconditionally of `output.value`:

```rust
// networks/bitcoin/src/wallet/mod.rs
if let Some(offset) = self.scripts.get(&output.script_pubkey) {
  res.push(ReceivedOutput {
    offset: *offset,
    output: output.clone(),
    outpoint: OutPoint::new(tx.compute_txid(), vout),
  });
}
```

`ReceivedOutput::value()` returns the raw satoshi amount, so this output is indistinguishable from any other received balance. The only dust checks anywhere in the wallet are on *outgoing* payments (`*amount < DUST` → `DustPayment` in `SignableTransaction::new`, and the `value >= DUST` test for the change output) — never on received inputs. When such an output is later supplied to `SignableTransaction::new`, `input_sat` includes its full value, yet each additional input adds ~230 weight units (≈57 vbytes) of signature/witness data, so `needed_fee = fee_per_vbyte * vbytes` rises by `fee_per_vbyte * 57` — more than the input is worth. The shortfall is silently paid by the change computation (`input_sat - payment_sat - fee_with_change`), i.e. by the multisig's other funds, since nothing in `new` filters inputs whose value is below their marginal fee contribution.

### Impact Explanation
- Funds reported received that are not spendable: a sub-dust output to the multisig address is reported by `scan_block`/`scan_transaction` as received balance, yet can never be economically redeemed (spending it requires paying more fee than it contains).
- Value drain: if the scheduler/wallet includes the dust input in a `SignableTransaction`, the difference between its value and its marginal fee cost is burned to miners out of the multisig's other inputs — the inverse of the Allo bug (the depositor externalizes the spend cost onto the protocol instead of paying the fee proportionate to their deposit).

### Likelihood Explanation
Any external party can craft a Bitcoin transaction paying a tiny amount (1–545 sats, or even above `DUST` but below `57 * fee_per_vbyte`) to the multisig's P2TR script. This is a normal on-chain transaction requiring no privileges, collusion, or malformed encodings. On low-fee chains the cost to the attacker is trivially small, and repeated dust deposits compound the unspendable-balance problem, mirroring the original report's "repeated minimal deposits" pattern.

### Recommendation
In `Scanner::scan_transaction` (or in `SignableTransaction::new`), reject/skip received outputs whose `value` is below the dust floor, and ideally below the marginal input spend cost `fee_per_vbyte * 57` (≈230 WU per Taproot key-spend input). Alternatively, have `SignableTransaction::new` drop inputs whose value is less than `fee_per_vbyte * marginal_input_vbytes` before computing `needed_fee`, so they are never treated as contributing `input_sat`.

### Proof of Concept
```rust
// Attacker broadcasts a TX with output: TxOut { value: Amount::from_sat(100),
//   script_pubkey: p2tr_script_buf(multisig_key) }
let outputs = scanner.scan_transaction(&attacker_tx);
// outputs[0].value() == 100 -> reported as received balance
assert_eq!(outputs.len(), 1);

// Later spending it: marginal input weight ~230 WU (~57 vbytes).
// At fee_per_vbyte = 1, input_sat += 100 but needed_fee += 57;
// at any realistic fee rate > 2 sat/vB, spending it nets negative,
// and the deficit is silently drawn from the change/other inputs:
//   change_value = input_sat - payment_sat - fee_with_change
// No code path rejects or skips this input.
```