[1](#0-0) [2](#0-1)

### Citations

**File:** types/src/aggregate_signature.rs (L90-133)
```rust
/// Partial signature from a set of validators. This struct is only used when aggregating the votes
/// from different validators. It is only kept in memory and never sent through the network.
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
pub struct PartialSignatures {
    signatures: BTreeMap<AccountAddress, bls12381::Signature>,
}

impl PartialSignatures {
    pub fn new(signatures: BTreeMap<AccountAddress, bls12381::Signature>) -> Self {
        Self { signatures }
    }

    pub fn empty() -> Self {
        Self::new(BTreeMap::new())
    }

    pub fn is_empty(&self) -> bool {
        self.signatures.is_empty()
    }

    pub fn remove_signature(&mut self, validator: AccountAddress) -> Option<bls12381::Signature> {
        self.signatures.remove(&validator)
    }

    pub fn add_signature(&mut self, validator: AccountAddress, signature: bls12381::Signature) {
        self.signatures.insert(validator, signature);
    }

    pub fn unpack(self) -> BTreeMap<AccountAddress, bls12381::Signature> {
        self.signatures
    }

    pub fn signatures_iter(&self) -> impl Iterator<Item = (&AccountAddress, &bls12381::Signature)> {
        self.signatures.iter()
    }

    pub fn signatures(&self) -> &BTreeMap<AccountAddress, bls12381::Signature> {
        &self.signatures
    }

    pub fn contains_voter(&self, voter: &AccountAddress) -> bool {
        self.signatures.contains_key(voter)
    }
}
```

**File:** consensus/src/pending_votes.rs (L58-62)
```rust
#[derive(Debug, PartialEq, Eq)]
pub enum VoteStatus {
    EnoughVotes(LedgerInfoWithSignatures),
    NotEnoughVotes(SignatureAggregator<LedgerInfo>),
}
```
