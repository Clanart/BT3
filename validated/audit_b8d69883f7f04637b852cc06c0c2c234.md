### Title
`Scanner::scan_block` reports immature coinbase outputs as spendable received funds — ([File: networks/bitcoin/src/wallet/mod.rs](networks/bitcoin/src/wallet/mod.rs))

### Summary
`Scanner::scan_block` iterates over **all** transactions in a block, including `block.txdata[0]` (the coinbase). Outputs paying to a Serai key inside a coinbase transaction are returned as `ReceivedOutput`s indistinguishable from normal outputs, yet they are unspendable by consensus for 100 blocks (coinbase maturity). Any consumer that treats scan results as immediately spendable inputs — e.g. feeding them into `SignableTransaction::new` — will build transactions that are rejected by the network, and may permanently mis-account funds as available.

### Finding Description
`scan_block` is defined at `networks/bitcoin/src/wallet/mod.rs` lines 221–227:

```rust
pub fn scan_block(&self, block: &Block) -> Vec<ReceivedOutput> {
  let mut res = Vec::new();
  for tx in &block.txdata {
    res.extend(self.scan_transaction(tx));
  }
  res
}
```

It scans `block.txdata` from index 0, so the coinbase is included. The doc comment acknowledges the issue ("This will also scan the coinbase transaction which is bound by maturity") but places the burden on the caller, and the returned `ReceivedOutput` carries no flag distinguishing coinbase-derived outputs.

Notably, the in-repo processor code does handle this correctly — `get_outputs` in `processor/src/networks/bitcoin.rs` line 691 explicitly iterates `&block.txdata[1 ..]` with the comment "Skip the coinbase transaction which is burdened by maturity". This confirms the maturity constraint is real and load-bearing in this codebase; the wallet `Scanner` API itself, however, produces the dangerous result. The library is the intended public surface (`pub mod wallet` in `networks/bitcoin/src/lib.rs` line 18), and nothing prevents an unprivileged miner from creating a coinbase output paying to a Serai address — miners choose their coinbase outputs freely.

Additionally, `scan_transaction` (`mod.rs` lines 199–214) matches only `script_pubkey`, so a coinbase output to the external key (offset `Scalar::ZERO`) is indistinguishable from a confirmed, spendable deposit.

### Impact Explanation
This is the direct analog of the referenced "funds sent cannot be retrieved / funds unavailable" class: `scan_block` reports funds as received that are not spendable. A downstream wallet or coordinator that calls `scan_block` (the natural API for block-level scanning) and passes results to `SignableTransaction::new` will produce a transaction spending an immature coinbase input. `SignableTransaction::new` performs no maturity check — it only checks values (`NotEnoughFunds`, dust) — so the signed transaction will be rejected by every Bitcoin node with `bad-txns-premature-spend-of-coinbase`. Depending on retry/rotation behavior, the funds can be treated as available balance while every spend attempt fails, effectively freezing them until maturity or until the consumer implements the undocumented-in-code filtering the doc comment vaguely suggests.

### Likelihood Explanation
Medium. A miner must include an output to the Serai key in a coinbase — this is fully within an unprivileged miner's control (miners who are also pool participants can pay arbitrary scripts, and pool payout coinbases frequently contain many outputs). The vulnerable path requires a consumer to use `scan_block` rather than `scan_transaction` on `txdata[1 ..]`; the API presents `scan_block` as the natural choice and its only guard is a prose doc comment, while the struct fields (`Scanner::scripts`) are private so callers cannot post-filter by offset vs. coinbase themselves except by re-scanning and comparing.

### Recommendation
Have `scan_block` skip `block.txdata[0]` unconditionally, or mark `ReceivedOutput`s originating from a coinbase (e.g. a `coinbase: bool` field or separate return channel) so callers cannot mistake immature outputs for spendable ones. Alternatively, track maturity in `SignableTransaction::new`/`multisig` and reject coinbase outpoints younger than 100 blocks.

### Proof of Concept
```rust
// Conceptual: a miner crafts a block whose coinbase pays to `key`'s P2TR script.
let key: ProjectivePoint = /* even Serai group key */;
let scanner = Scanner::new(key).unwrap();
let coinbase_script = p2tr_script_buf(key).unwrap();

let block: Block = /* block where txdata[0].output[0].script_pubkey == coinbase_script */;

let outputs = scanner.scan_block(&block);
// outputs[0] is a ReceivedOutput for the coinbase UTXO.
// No maturity information is exposed:
assert_eq!(outputs[0].offset(), Scalar::ZERO);

// Consumer builds a spend:
let tx = SignableTransaction::new(
  vec![outputs[0].clone()], &payments, change, None, fee,
).unwrap(); // succeeds — no maturity check

// After FROST signing, broadcast fails:
// bitcoin node rejects with bad-txns-premature-spend-of-coinbase.
```

Relevant code: `Scanner::scan_block` at `networks/bitcoin/src/wallet/mod.rs:221-227`; `Scanner::scan_transaction` matching only `script_pubkey` at `mod.rs:199-214`; the contrast with the correct handling at `processor/src/networks/bitcoin.rs:691` (`&block.txdata[1 ..]`); absence of any maturity/coinbase check in `SignableTransaction::new` at `networks/bitcoin/src/wallet/send.rs:150-256`.