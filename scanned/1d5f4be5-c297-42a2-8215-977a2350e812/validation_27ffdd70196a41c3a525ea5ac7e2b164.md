[1](#0-0) [2](#0-1)

### Citations

**File:** core/src/consensus/tower1_7_14.rs (L19-24)
```rust
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq)]
pub struct Tower1_7_14 {
    pub(crate) node_pubkey: Pubkey,
    pub(crate) threshold_depth: usize,
    pub(crate) threshold_size: f64,
    pub(crate) vote_state: VoteState1_14_11,
```

**File:** core/src/consensus/tower1_7_14.rs (L61-69)
```rust
impl SavedTower1_7_14 {
    pub fn new<T: Signer>(tower: &Tower1_7_14, keypair: &T) -> Result<Self> {
        let node_pubkey = keypair.pubkey();
        if tower.node_pubkey != node_pubkey {
            return Err(TowerError::WrongTower(format!(
                "node_pubkey is {:?} but found tower for {:?}",
                node_pubkey, tower.node_pubkey
            )));
        }
```
