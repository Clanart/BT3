[1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** core/src/consensus/tower_storage.rs (L17-65)
```rust
#[cfg_attr(feature = "frozen-abi", derive(AbiExample, StableAbi, StableAbiSample))]
#[derive(Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub enum SavedTowerVersions {
    V1_17_14(SavedTower1_7_14),
    Current(SavedTower),
}

impl SavedTowerVersions {
    fn try_into_tower(&self, node_pubkey: &Pubkey) -> Result<Tower> {
        // This method assumes that `self` was just deserialized
        assert_eq!(self.pubkey(), Pubkey::default());

        let tv = match self {
            SavedTowerVersions::V1_17_14(t) => {
                if !t.signature.verify(node_pubkey.as_ref(), &t.data) {
                    return Err(TowerError::InvalidSignature);
                }
                bincode::deserialize(&t.data).map(TowerVersions::V1_7_14)
            }
            SavedTowerVersions::Current(t) => {
                if !t.signature.verify(node_pubkey.as_ref(), &t.data) {
                    return Err(TowerError::InvalidSignature);
                }
                bincode::deserialize(&t.data).map(TowerVersions::V1_14_11)
            }
        };
        tv.map_err(|e| e.into()).and_then(|tv: TowerVersions| {
            let tower = tv.convert_to_current();
            if tower.node_pubkey != *node_pubkey {
                return Err(TowerError::WrongTower(format!(
                    "node_pubkey is {:?} but found tower for {:?}",
                    node_pubkey, tower.node_pubkey
                )));
            }
            Ok(tower)
        })
    }

    fn serialize_into(&self, file: &mut File) -> Result<()> {
        bincode::serialize_into(file, self).map_err(|e| e.into())
    }

    fn pubkey(&self) -> Pubkey {
        match self {
            SavedTowerVersions::V1_17_14(t) => t.node_pubkey,
            SavedTowerVersions::Current(t) => t.node_pubkey,
        }
    }
}
```

**File:** core/src/consensus/tower_storage.rs (L87-94)
```rust
#[derive(Default, Clone, Serialize, Deserialize, Debug, PartialEq, Eq)]
pub struct SavedTower {
    signature: Signature,
    #[serde(with = "serde_bytes")]
    data: Vec<u8>,
    #[serde(skip)]
    node_pubkey: Pubkey,
}
```

**File:** core/src/consensus/tower_storage.rs (L182-206)
```rust
    fn load(&self, node_pubkey: &Pubkey) -> Result<Tower> {
        let filename = self.filename(node_pubkey);
        trace!("load {}", filename.display());

        // Ensure to create parent dir here, because restore() precedes save() always
        fs::create_dir_all(filename.parent().unwrap())?;

        if let Ok(file) = File::open(&filename) {
            // New format
            let mut stream = BufReader::new(file);

            bincode::deserialize_from(&mut stream)
                .map_err(|e| e.into())
                .and_then(|t: SavedTowerVersions| t.try_into_tower(node_pubkey))
        } else {
            // Old format
            let file = File::open(self.old_filename(node_pubkey))?;
            let mut stream = BufReader::new(file);
            bincode::deserialize_from(&mut stream)
                .map_err(|e| e.into())
                .and_then(|t: SavedTower1_7_14| {
                    SavedTowerVersions::from(t).try_into_tower(node_pubkey)
                })
        }
    }
```

**File:** core/src/consensus/tower_storage.rs (L208-223)
```rust
    fn store(&self, saved_tower: &SavedTowerVersions) -> Result<()> {
        let pubkey = saved_tower.pubkey();
        let filename = self.filename(&pubkey);
        trace!("store: {}", filename.display());
        let new_filename = filename.with_extension("bin.new");

        {
            // overwrite anything if exists
            let mut file = File::create(&new_filename)?;
            saved_tower.serialize_into(&mut file)?;
            // file.sync_all() hurts performance; pipeline sync-ing and submitting votes to the cluster!
        }
        fs::rename(&new_filename, &filename)?;
        // self.path.parent().sync_all() hurts performance same as the above sync
        Ok(())
    }
```

**File:** core/src/consensus.rs (L1684-1692)
```rust
    pub fn save(&self, tower_storage: &dyn TowerStorage, node_keypair: &Keypair) -> Result<()> {
        let saved_tower = SavedTower::new(self, node_keypair)?;
        tower_storage.store(&SavedTowerVersions::from(saved_tower))?;
        Ok(())
    }

    pub fn restore(tower_storage: &dyn TowerStorage, node_pubkey: &Pubkey) -> Result<Self> {
        tower_storage.load(node_pubkey)
    }
```

**File:** core/src/replay_stage/tests.rs (L6293-6315)
```rust
#[test]
fn test_tower_load_missing() {
    let tower_file = tempdir().unwrap().keep();
    let tower_storage = FileTowerStorage::new(tower_file);
    let node_pubkey = Pubkey::new_unique();
    let vote_account = Pubkey::new_unique();
    let tree = tr(0) / (tr(1) / (tr(3) / (tr(4))) / (tr(2) / (tr(5) / (tr(6)))));
    let generate_votes = |pubkeys: Vec<Pubkey>| {
        pubkeys
            .into_iter()
            .zip(iter::once(vec![0, 1, 2, 5, 6]).chain(iter::repeat_n(vec![0, 1, 3, 4], 2)))
            .collect()
    };
    let (vote_simulator, _blockstore) =
        setup_forks_from_tree(tree, 3, Some(Box::new(generate_votes)));
    let bank_forks = vote_simulator.bank_forks;

    let tower =
        ReplayStage::load_tower(&tower_storage, &node_pubkey, &vote_account, &bank_forks).unwrap();
    let expected_tower = Tower::new_for_tests(VOTE_THRESHOLD_DEPTH, VOTE_THRESHOLD_SIZE);
    assert_eq!(tower.vote_state, expected_tower.vote_state);
    assert_eq!(tower.node_pubkey, node_pubkey);
}
```
