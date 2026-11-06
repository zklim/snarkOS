// Copyright (c) 2019-2026 Provable Inc.
// This file is part of the snarkOS library.

// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at:

// http://www.apache.org/licenses/LICENSE-2.0

// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

use super::{Developer, StoreFormat};
use crate::helpers::dev::get_development_key;
use anyhow::{Context, Result, anyhow, bail, ensure};
use clap::{Args, Parser, Subcommand, ValueEnum};
use fn_dsa::{DOMAIN_NONE, HASH_ID_RAW, KeyPairGenerator, KeyPairGenerator512, SigningKey, SigningKey512};
use ml_dsa::{KeyExport, Keypair, MlDsa65, Signer};
use rand_core06::{OsRng, RngCore};
use serde::{Deserialize, Serialize};
use snarkvm::{ledger::block::Recovery, prelude::*};
use snarkvm_recovery::{
    self as core,
    Backends,
    Digest64,
    Effects,
    Evidence,
    Key,
    Operation,
    Owner,
    Policy,
    Profile,
    RecordVersion,
};
use snarkvm_recovery_records::PreparationSource;
use snarkvm_recovery_signatures::{Falcon512Verifier, MlDsa65Verifier};
use std::{fs, io::Write, path::PathBuf, str::FromStr};
use ureq::http::Uri;
use zeroize::{Zeroize, ZeroizeOnDrop, Zeroizing};

/// Constructs disclosed credits recovery transactions. Signing secrets remain local.
#[derive(Debug, Parser)]
pub struct RecoveryCommand {
    #[command(subcommand)]
    command: RecoveryAction,
}

#[derive(Debug, Subcommand)]
enum RecoveryAction {
    /// Generate a local PQ signing key file with owner-only permissions.
    Keygen(GenerateKey),
    /// Export genuine historical private credits openings owned by this account.
    Sources(SourceArgs),
    /// Authorize preparation, migration, spending or enrolled-key rotation.
    Build(BuildArgs),
    /// Construct a canonical negative-test envelope without refreshing authorization.
    #[cfg(feature = "test_network")]
    #[command(hide = true)]
    TestMutation(MutationArgs),
}

#[cfg(feature = "test_network")]
#[derive(Clone, Copy, Debug, ValueEnum)]
enum Mutation {
    Effects,
    Signature,
    Witness,
    Network,
    Key,
    Replay,
    Authorization,
}

#[cfg(feature = "test_network")]
#[derive(Debug, Args)]
struct MutationArgs {
    #[arg(long)]
    transaction: PathBuf,
    #[arg(long)]
    store: PathBuf,
    #[arg(long, value_enum)]
    mutation: Mutation,
    #[arg(long)]
    key_file: Option<PathBuf>,
    #[arg(long)]
    replay: Option<String>,
    #[arg(long)]
    state_file: Option<PathBuf>,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Algorithm {
    MlDsa65,
    Falcon512,
}

#[derive(Debug, Args)]
struct GenerateKey {
    #[arg(long, value_enum)]
    algorithm: Algorithm,
    #[arg(long)]
    key_file: PathBuf,
}

#[derive(Debug, Args)]
#[group(required = true, multiple = false)]
struct LegacyKey {
    /// File containing an Aleo private key; never sent to a validator.
    #[arg(long)]
    private_key_file: Option<PathBuf>,
    /// Public local-development account only.
    #[arg(long)]
    dev_key: Option<u16>,
}

impl LegacyKey {
    fn read<N: Network>(&self) -> Result<PrivateKey<N>> {
        match (&self.private_key_file, self.dev_key) {
            (Some(path), None) => {
                let text = Zeroizing::new(fs::read_to_string(path)?);
                PrivateKey::from_str(text.trim())
            }
            (None, Some(index)) => get_development_key(index),
            _ => bail!("Select exactly one legacy account key source"),
        }
    }
}

#[derive(Debug, Args)]
struct SourceArgs {
    #[command(flatten)]
    account: LegacyKey,
    #[arg(long, default_value = "http://127.0.0.1:3030")]
    endpoint: Uri,
    #[arg(long, default_value_t = 0)]
    height: u32,
    /// New directory containing one disclosed source JSON per owned record.
    #[arg(long)]
    output_dir: PathBuf,
}

#[derive(Clone, Copy, Debug, ValueEnum)]
enum Action {
    Prepare,
    Migrate,
    Spend,
    Rotate,
}

impl From<Action> for Operation {
    fn from(action: Action) -> Self {
        match action {
            Action::Prepare => Self::Prepare,
            Action::Migrate => Self::Migrate,
            Action::Spend => Self::Spend,
            Action::Rotate => Self::Rotate,
        }
    }
}

#[derive(Debug, Args)]
struct BuildArgs {
    #[arg(value_enum)]
    action: Action,
    #[arg(long, default_value = "http://127.0.0.1:3030")]
    endpoint: Uri,
    /// Optional retained /recovery/state response for reproducible offline construction.
    #[arg(long)]
    state_file: Option<PathBuf>,
    /// Disclosed source JSON exported by `recovery sources`; preparation only.
    #[arg(long)]
    source_file: Option<PathBuf>,
    #[arg(long, conflicts_with = "private_key_file")]
    dev_key: Option<u16>,
    #[arg(long)]
    private_key_file: Option<PathBuf>,
    /// Input recovery record ID, in hexadecimal; migration, spending or rotation.
    #[arg(long)]
    input: Option<String>,
    /// Current enrolled signing key, or enrollment key for preparation.
    #[arg(long)]
    key_file: PathBuf,
    /// Replacement enrolled key. Its proof of possession is required as well.
    #[arg(long)]
    recipient_key_file: Option<PathBuf>,
    #[arg(long)]
    valid_from: Option<u32>,
    #[arg(long)]
    valid_until: Option<u32>,
    /// Explicit replay field for local conformance testing.
    #[cfg(feature = "test_network")]
    #[arg(long, hide = true)]
    test_replay: Option<String>,
    #[arg(long, default_value_t = 100_000)]
    fee: u64,
    /// Canonical production transaction JSON output.
    #[arg(long)]
    store: PathBuf,
    #[arg(long)]
    broadcast: bool,
    #[arg(long, requires = "broadcast")]
    wait: bool,
    #[arg(long, default_value_t = 120)]
    timeout: u64,
}

#[derive(Serialize, Deserialize, Zeroize, ZeroizeOnDrop)]
#[serde(deny_unknown_fields)]
struct SigningFile {
    algorithm: String,
    secret: Vec<u8>,
    public: Vec<u8>,
}

impl SigningFile {
    fn read(path: &PathBuf) -> Result<Self> {
        ensure!(fs::metadata(path)?.len() <= 32_768, "Signing key file exceeds limit");
        let bytes = Zeroizing::new(fs::read(path)?);
        let key: Self = serde_json::from_slice(&bytes)?;
        key.key()?.validate()?;
        ensure!(key.key()? == key.derived_public()?, "Signing key file has inconsistent public key");
        Ok(key)
    }

    fn key(&self) -> Result<Key> {
        let profile = match self.algorithm.as_str() {
            "ml-dsa-65" => Profile::ML_DSA_65,
            "falcon-512" => Profile::FALCON_512,
            _ => bail!("Unsupported signing profile"),
        };
        Ok(Key { profile, bytes: self.public.clone() })
    }

    fn derived_public(&self) -> Result<Key> {
        match self.algorithm.as_str() {
            "ml-dsa-65" => {
                let seed: [u8; 32] = self.secret.as_slice().try_into().context("Invalid ML-DSA seed length")?;
                let seed = Zeroizing::new(seed);
                let signing = ml_dsa::SigningKey::<MlDsa65>::from_seed(&(*seed).into());
                Ok(Key { profile: Profile::ML_DSA_65, bytes: signing.verifying_key().to_bytes().to_vec() })
            }
            "falcon-512" => {
                let signing =
                    SigningKey512::decode(&self.secret).ok_or_else(|| anyhow!("Invalid Falcon private key"))?;
                let mut public = vec![0; fn_dsa::vrfy_key_size(9)];
                signing.to_verifying_key(&mut public);
                Ok(Key { profile: Profile::FALCON_512, bytes: public })
            }
            _ => bail!("Unsupported signing profile"),
        }
    }

    fn sign(&self, message: &[u8]) -> Result<Vec<u8>> {
        match self.algorithm.as_str() {
            "ml-dsa-65" => {
                let seed: [u8; 32] = self.secret.as_slice().try_into().context("Invalid ML-DSA seed length")?;
                let seed = Zeroizing::new(seed);
                let signing = ml_dsa::SigningKey::<MlDsa65>::from_seed(&(*seed).into());
                let signature: ml_dsa::Signature<MlDsa65> = signing.sign(message);
                Ok(signature.encode().to_vec())
            }
            "falcon-512" => {
                let mut signing =
                    SigningKey512::decode(&self.secret).ok_or_else(|| anyhow!("Invalid Falcon private key"))?;
                let mut signature = vec![0; fn_dsa::signature_size(9)];
                signing.sign(&mut OsRng, &DOMAIN_NONE, &HASH_ID_RAW, message, &mut signature);
                Ok(signature)
            }
            _ => bail!("Unsupported signing profile"),
        }
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SourceFile {
    commitment: String,
    record: String,
}

#[derive(Deserialize)]
struct RecoveryState {
    height: u32,
    network: String,
    checkpoint: String,
    policy: PolicyJson,
}

#[derive(Deserialize)]
struct PolicyJson {
    preparation: Option<u32>,
    recovery: Option<u32>,
    profiles: Vec<[u16; 2]>,
    aggregates: Vec<u16>,
}

impl RecoveryState {
    fn policy(&self) -> Policy {
        Policy {
            preparation: self.policy.preparation,
            recovery: self.policy.recovery,
            profiles: self.policy.profiles.iter().map(|p| Profile { algorithm: p[0], parameters: p[1] }).collect(),
            aggregates: self.policy.aggregates.clone(),
        }
    }
}

impl RecoveryCommand {
    pub fn parse<N: Network>(self) -> Result<String> {
        match self.command {
            RecoveryAction::Keygen(args) => Self::generate_key(args),
            RecoveryAction::Sources(args) => Self::sources::<N>(args),
            RecoveryAction::Build(args) => Self::build::<N>(args),
            #[cfg(feature = "test_network")]
            RecoveryAction::TestMutation(args) => Self::mutate::<N>(args),
        }
    }

    #[cfg(feature = "test_network")]
    fn mutate<N: Network>(args: MutationArgs) -> Result<String> {
        ensure!(fs::metadata(&args.transaction)?.len() <= 16 * 1024 * 1024, "Transaction file exceeds limit");
        let transaction: Transaction<N> = serde_json::from_slice(&fs::read(&args.transaction)?)?;
        let recovery = transaction.recovery().ok_or_else(|| anyhow!("Mutation requires a recovery transaction"))?;
        let mut transaction = recovery.transaction().clone();
        let mut sources = recovery.sources().to_vec();
        match args.mutation {
            Mutation::Effects => {
                transaction.effects.fee = transaction.effects.fee.checked_add(1).context("Fee overflow")?;
                transaction.effects.outputs[0].amount =
                    transaction.effects.outputs[0].amount.checked_sub(1).context("Amount underflow")?;
            }
            Mutation::Signature => match &mut transaction.authorization {
                Evidence::Direct(signatures) => {
                    *signatures
                        .first_mut()
                        .and_then(|signature| signature.first_mut())
                        .ok_or_else(|| anyhow!("Missing direct signature"))? ^= 1
                }
                _ => bail!("Mutation requires direct authorization"),
            },
            Mutation::Witness => {
                sources.first_mut().ok_or_else(|| anyhow!("Missing historical source"))?.response += Scalar::one();
            }
            Mutation::Network => transaction.effects.network[0] ^= 1,
            Mutation::Key => {
                let path = args.key_file.as_ref().ok_or_else(|| anyhow!("Key mutation requires --key-file"))?;
                transaction.effects.outputs[0].owner = Owner::Enrolled(SigningFile::read(path)?.key()?);
            }
            Mutation::Replay => {
                transaction.effects.replay =
                    digest64(args.replay.as_ref().ok_or_else(|| anyhow!("Replay mutation requires --replay"))?)?
            }
            Mutation::Authorization => {
                let key = SigningFile::read(
                    args.key_file.as_ref().ok_or_else(|| anyhow!("Authorization mutation requires --key-file"))?,
                )?;
                let path =
                    args.state_file.as_ref().ok_or_else(|| anyhow!("Authorization mutation requires --state-file"))?;
                ensure!(fs::metadata(path)?.len() <= 128 * 1024 * 1024, "Recovery snapshot exceeds limit");
                let state: RecoveryState = serde_json::from_slice(&fs::read(path)?)?;
                let network = digest64(&state.network)?;
                let mut backends = Backends::default();
                backends.register_signature(Box::new(MlDsa65Verifier))?;
                backends.register_signature(Box::new(Falcon512Verifier))?;
                let ledger =
                    core::Ledger::restore(network, state.policy(), &hex::decode(&state.checkpoint)?, &backends)?;
                let plan = ledger.plan(
                    state.height.saturating_add(1).max(transaction.effects.valid_from),
                    &transaction.effects,
                    &backends,
                )?;
                transaction.authorization = Evidence::Direct(
                    plan.signatures
                        .iter()
                        .map(|statement| key.sign(statement.message()))
                        .collect::<Result<Vec<_>>>()?,
                );
            }
        }
        let transaction = Transaction::from_recovery(Recovery::new(transaction, sources)?)?;
        fs::write(&args.store, serde_json::to_vec_pretty(&transaction)?)?;
        Ok(serde_json::json!({"transaction_id":transaction.id().to_string(),"store":args.store}).to_string())
    }

    fn generate_key(args: GenerateKey) -> Result<String> {
        let key = match args.algorithm {
            Algorithm::MlDsa65 => {
                let mut seed = Zeroizing::new(vec![0; 32]);
                OsRng.fill_bytes(&mut seed);
                let mut file = SigningFile { algorithm: "ml-dsa-65".into(), secret: seed.to_vec(), public: vec![] };
                file.public = file.derived_public()?.bytes;
                file
            }
            Algorithm::Falcon512 => {
                let mut private = Zeroizing::new(vec![0; fn_dsa::sign_key_size(9)]);
                let mut public = vec![0; fn_dsa::vrfy_key_size(9)];
                KeyPairGenerator512::default().keygen(9, &mut OsRng, &mut private, &mut public);
                SigningFile { algorithm: "falcon-512".into(), secret: private.to_vec(), public }
            }
        };
        let mut options = fs::OpenOptions::new();
        options.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.mode(0o600);
        }
        let bytes = Zeroizing::new(serde_json::to_vec_pretty(&key)?);
        let mut file = options.open(&args.key_file)?;
        file.write_all(&bytes)?;
        file.sync_all()?;
        Ok(serde_json::json!({"key_file":args.key_file,"algorithm":key.algorithm,"public_key":hex::encode(&key.public)}).to_string())
    }

    fn sources<N: Network>(args: SourceArgs) -> Result<String> {
        let account = args.account.read::<N>()?;
        let view_key = ViewKey::try_from(account)?;
        let block: Block<N> = Developer::http_get_json::<N, _>(&args.endpoint, &format!("block/{}", args.height))?
            .ok_or_else(|| anyhow!("Source block not found"))?;
        fs::create_dir(&args.output_dir)?;
        let mut sources = vec![];
        for (commitment, ciphertext) in block.records() {
            if let Ok(record) = ciphertext.decrypt(&view_key) {
                let path = args.output_dir.join(format!("source-{}.json", sources.len()));
                let source = SourceFile { commitment: commitment.to_string(), record: record.to_string() };
                fs::write(&path, serde_json::to_vec_pretty(&source)?)?;
                sources.push(serde_json::json!({"path":path,"commitment":commitment.to_string(),"version":record.version().to_string()}));
            }
        }
        ensure!(!sources.is_empty(), "No records owned by this account in source block");
        Ok(serde_json::json!({"block_hash":block.hash().to_string(),"height":args.height,"sources":sources})
            .to_string())
    }

    fn build<N: Network>(args: BuildArgs) -> Result<String> {
        let state: RecoveryState = match &args.state_file {
            Some(path) => {
                ensure!(fs::metadata(path)?.len() <= 128 * 1024 * 1024, "Recovery snapshot exceeds limit");
                serde_json::from_slice(&fs::read(path)?)?
            }
            None => Developer::http_get_json::<N, _>(&args.endpoint, "recovery/state")?
                .ok_or_else(|| anyhow!("Recovery state endpoint not found"))?,
        };
        let network = digest64(&state.network)?;
        let policy = state.policy();
        policy.validate()?;
        let mut backends = Backends::default();
        backends.register_signature(Box::new(MlDsa65Verifier))?;
        backends.register_signature(Box::new(Falcon512Verifier))?;
        let checkpoint = hex::decode(&state.checkpoint)?;
        let mut ledger = core::Ledger::restore(network, policy.clone(), &checkpoint, &backends)?;
        let signing = SigningFile::read(&args.key_file)?;
        let recipient = args.recipient_key_file.as_ref().map(SigningFile::read).transpose()?;
        let recipient_key = recipient.as_ref().unwrap_or(&signing).key()?;
        let operation = Operation::from(args.action);
        let valid_from = args.valid_from.unwrap_or(state.height.saturating_add(1));
        let valid_until = args.valid_until.unwrap_or(valid_from.saturating_add(100));
        let mut preparation = None;
        let (input, amount, version) = match operation {
            Operation::Prepare => {
                ensure!(args.input.is_none(), "Preparation takes a historical source, not a recovery input ID");
                let path = args.source_file.as_ref().ok_or_else(|| anyhow!("Preparation requires --source-file"))?;
                ensure!(fs::metadata(path)?.len() <= 32_768, "Source file exceeds limit");
                let source: SourceFile = serde_json::from_slice(&fs::read(path)?)?;
                let account =
                    LegacyKey { private_key_file: args.private_key_file.clone(), dev_key: args.dev_key }.read::<N>()?;
                let record = Record::<N, Plaintext<N>>::from_str(&source.record)?;
                let commitment = Field::<N>::from_str(&source.commitment)?;
                let view_key = ViewKey::try_from(account)?;
                let rvk = (*record.nonce() * *view_key).to_x_coordinate();
                let source_record = PreparationSource::record_from_opening(&network, &record, rvk, commitment)?;
                let input = source_record.id(&network);
                let amount = source_record.amount;
                ledger.import_verified_source(source_record, &backends)?;
                preparation = Some((account, record, rvk, commitment));
                (input, amount, RecordVersion::V2)
            }
            Operation::Migrate | Operation::Spend | Operation::Rotate => {
                ensure!(
                    args.source_file.is_none() && args.dev_key.is_none() && args.private_key_file.is_none(),
                    "Legacy source/key arguments are only valid for preparation"
                );
                let input = digest64(args.input.as_ref().ok_or_else(|| anyhow!("Operation requires --input"))?)?;
                let previous = ledger.unspent(&input).ok_or_else(|| anyhow!("Input is absent or spent"))?;
                ensure!(previous.owner == Owner::Enrolled(signing.key()?), "Signing key does not match enrolled owner");
                let version = if operation == Operation::Rotate { previous.version } else { RecordVersion::V3 };
                (input, previous.amount, version)
            }
        };
        let amount = amount.checked_sub(args.fee).ok_or_else(|| anyhow!("Fee exceeds input value"))?;
        let mut nonce = [0; 64];
        let mut replay = [0; 64];
        OsRng.fill_bytes(&mut nonce);
        OsRng.fill_bytes(&mut replay);
        #[cfg(feature = "test_network")]
        if let Some(encoded) = &args.test_replay {
            replay = digest64(encoded)?;
        }
        let output = core::Record {
            version,
            owner: Owner::Enrolled(recipient_key.clone()),
            amount,
            nonce,
            opening: core::digest(b"AleoDisclosedCreditsOutput1", &[
                &network,
                &nonce,
                &amount.to_le_bytes(),
                &recipient_key.identity(&network),
            ]),
        };
        let output_id = output.id(&network);
        let effects = Effects {
            network,
            protocol: core::PROTOCOL_VERSION,
            policy: 1,
            policy_commitment: policy.commitment(&network),
            operation,
            valid_from,
            valid_until,
            replay,
            inputs: vec![input],
            outputs: vec![output],
            fee: args.fee,
        };
        let plan = ledger.plan(valid_from, &effects, &backends)?;
        let mut sources = vec![];
        let mut legacy = vec![];
        if let Some((account, record, rvk, commitment)) = preparation {
            ensure!(plan.legacy.len() == 1, "Preparation plan does not have exactly one legacy statement");
            let message = &plan.legacy[0].message;
            let rng = &mut rand::rng();
            sources.push(PreparationSource::create(&account, record, rvk, commitment, &network, message, rng)?);
            legacy.push(Signature::sign_bytes(&account, message, rng)?.to_bytes_le()?);
        }
        let signatures = plan
            .signatures
            .iter()
            .map(|statement| {
                let signer = if signing.key()? == *statement.key() {
                    &signing
                } else {
                    recipient
                        .as_ref()
                        .filter(|r| r.key().is_ok_and(|key| key == *statement.key()))
                        .ok_or_else(|| anyhow!("No signing key for core-determined statement"))?
                };
                let signature = signer.sign(statement.message())?;
                backends.verify(statement, &signature)?;
                Ok(signature)
            })
            .collect::<Result<Vec<_>>>()?;
        let recovery =
            Recovery::new(core::Transaction { effects, legacy, authorization: Evidence::Direct(signatures) }, sources)?;
        let minimum_fee = recovery.minimum_fee()?;
        ensure!(args.fee >= minimum_fee, "Fee is below the canonical resource minimum ({minimum_fee})");
        let full_id = hex::encode(recovery.digest()?);
        let transaction = Transaction::from_recovery(recovery)?;
        let transaction_id = transaction.id();
        Developer::handle_transaction::<N>(
            Some(&args.endpoint),
            &args.broadcast.then_some(None),
            false,
            &Some(args.store.to_string_lossy().into_owned()),
            StoreFormat::String,
            args.wait,
            args.timeout,
            transaction,
            format!("recovery {operation:?}"),
        )?;
        Ok(serde_json::json!({"transaction_id":transaction_id.to_string(),"recovery_id":full_id,"input":hex::encode(input),"output":hex::encode(output_id),"output_amount":amount,"fee":args.fee,"minimum_fee":minimum_fee,"version":version as u8,"replay":hex::encode(replay),"store":args.store}).to_string())
    }
}

fn digest64(encoded: &str) -> Result<Digest64> {
    hex::decode(encoded)?.try_into().map_err(|_| anyhow!("Expected a 64-byte hexadecimal digest"))
}
